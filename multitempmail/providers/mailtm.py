"""mail.tm provider — https://docs.mail.tm

API summary (as documented):

* ``GET /domains`` returns a Hydra collection of available domains; a
  domain must be chosen before an account is created.
* ``POST /accounts {address, password}`` → 201, then ``POST /token
  {address, password}`` → ``{id, token}``; afterwards every request uses
  ``Authorization: Bearer <token>``.
* ``GET /messages`` (30 per page, ``hydra:member``), ``GET /messages/{id}``
  (full message), ``PATCH /messages/{id} {seen: true}``,
  ``DELETE /messages/{id}``, ``GET /me``, ``DELETE /accounts/{id}``.
* Attachments expose a (relative) ``downloadUrl`` that is fetched directly.
* Errors: 400, 401, 404, 418, 422, 429. Rate limit: 8 requests/s.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, register
from ..caps import Cap
from ..errors import AuthenticationError, MailboxExpired, MessageNotFound, ProviderError
from ..models import Address, Attachment, Mailbox, Message
from ..utils import parse_dt, random_local_part, random_password


@register("mailtm")
class MailTmProvider(BaseProvider):
    """Mail.tm provider (https://mail.tm).

    Mailboxes are real accounts: ``create()`` picks an active public
    domain, creates the account with a generated password and authenticates
    to obtain a Bearer token, which is stored on the returned
    :class:`~multitempmail.models.Mailbox`. ``attach()`` accepts either a
    ``token`` or a ``password`` (re-authenticated via ``POST /token``).
    """

    name = "mailtm"
    display_name = "Mail.tm"
    base_url = "https://api.mail.tm"
    # Real API (account + token + password) with the richest capabilities, but
    # rate-limited to 10 requests/minute and ~1-3 s per call, so it is not the
    # first choice. It is the reliable path when another provider lets you down.
    default_priority = 90
    capabilities = frozenset(
        {
            Cap.CREATE,
            Cap.LIST,
            Cap.READ,
            Cap.DELETE_MESSAGE,
            Cap.DELETE_MAILBOX,
            Cap.MARK_READ,
            Cap.ATTACHMENTS,
            Cap.DOMAIN_SELECT,
        }
    )

    def domains(self) -> list[dict[str, Any]]:
        """List available domains (the ``hydra:member`` of ``GET /domains``)."""
        return self._cache_call(
            f"provider:{self.name}:domains",
            lambda: list(self._request("GET", "/domains").json().get("hydra:member", [])),
        )

    def create(  # type: ignore[override]
        self,
        *,
        domain: str | None = None,
        local_part: str | None = None,
        password: str | None = None,
    ) -> Mailbox:
        """Create an account and return an authenticated Mailbox.

        Without ``domain``, the first active public domain from
        ``GET /domains`` is used. A random password is generated unless
        provided.
        """
        if domain is None:
            active = [d for d in self.domains() if d.get("isActive")]
            active.sort(key=lambda d: bool(d.get("isPrivate")))
            if not active:
                raise ProviderError("mail.tm returned no active domains")
            domain = active[0]["domain"]
        password = password or random_password()
        local = local_part or random_local_part()
        address = f"{local}@{domain}"
        account = self._request(
            "POST", "/accounts", json={"address": address, "password": password}
        ).json()
        token = self._issue_token(address, password)
        return Mailbox(
            provider=self.name,
            address=address,
            token=token,
            password=password,
            created_at=parse_dt(account.get("createdAt")),
            raw={"account_id": account.get("id"), "account": account},
            _provider=self,
        )

    def attach(
        self,
        address: str,
        token: str | None = None,
        password: str | None = None,
        **extra: Any,
    ) -> Mailbox:
        """Attach to an existing account.

        Requires ``token`` (the Bearer token) or ``password`` (used to
        re-authenticate via ``POST /token``). ``account_id`` may be passed
        as an extra keyword to avoid a ``GET /me`` round-trip on
        :meth:`delete_mailbox`.
        """
        account_id = extra.pop("account_id", None)
        if token is None:
            if not password:
                raise AuthenticationError(
                    "mail.tm requires a mailbox token (or a password to re-authenticate)"
                )
            auth = self._request(
                "POST", "/token", json={"address": address, "password": password}
            ).json()
            token = auth.get("token")
            if isinstance(token, dict):
                token = token.get("id")
            account_id = account_id or auth.get("id")
        return Mailbox(
            provider=self.name,
            address=address,
            token=token,
            password=password,
            raw={"account_id": account_id},
            _provider=self,
        )

    def _issue_token(self, address: str, password: str) -> str:
        auth = self._request(
            "POST", "/token", json={"address": address, "password": password}
        ).json()
        token = auth.get("token")
        if isinstance(token, dict):
            token = token.get("id")
        if not token:
            raise AuthenticationError("mail.tm token endpoint returned no token")
        return token

    def _auth_headers(self, mailbox: Mailbox) -> dict[str, str]:
        if not mailbox.token:
            raise AuthenticationError(
                "mail.tm mailbox has no token; re-create it or attach with token=/password="
            )
        return {"Authorization": f"Bearer {mailbox.token}"}

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List messages (30 per page, paginated until exhausted)."""
        headers = self._auth_headers(mailbox)
        page = 1
        out: list[Message] = []
        while True:
            data = self._request("GET", "/messages", params={"page": page}, headers=headers).json()
            items = list(data.get("hydra:member", []))
            out.extend(self._map_list_item(item) for item in items)
            if len(items) < 30:
                break
            page += 1
        return out

    @staticmethod
    def _map_list_item(item: dict[str, Any]) -> Message:
        from_ = item.get("from") or {}
        return Message(
            id=str(item["id"]),
            sender=from_.get("address") if isinstance(from_, dict) else (from_ or None),
            sender_name=from_.get("name") if isinstance(from_, dict) else None,
            to=tuple(
                Address(name=a.get("name"), address=a.get("address"))
                for a in (item.get("to") or [])
            ),
            subject=item.get("subject"),
            seen=item.get("seen"),
            size=item.get("size"),
            created_at=parse_dt(item.get("createdAt")),
            raw=item,
        )

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a full message (text body, html blocks, attachments)."""
        headers = self._auth_headers(mailbox)
        item = self._request("GET", f"/messages/{message_id}", headers=headers).json()
        html = item.get("html") or []
        if isinstance(html, list):
            html = "".join(str(block) for block in html)
        from_ = item.get("from") or {}
        attachments = tuple(
            Attachment(
                id=str(a.get("id")) if a.get("id") is not None else None,
                filename=a.get("filename") or "",
                content_type=a.get("contentType"),
                size=a.get("size"),
                download_url=a.get("downloadUrl"),
            )
            for a in (item.get("attachments") or [])
        )
        return Message(
            id=str(item["id"]),
            sender=from_.get("address") if isinstance(from_, dict) else (from_ or None),
            sender_name=from_.get("name") if isinstance(from_, dict) else None,
            to=tuple(
                Address(name=a.get("name"), address=a.get("address"))
                for a in (item.get("to") or [])
            ),
            cc=tuple(
                Address(name=a.get("name"), address=a.get("address"))
                for a in (item.get("cc") or [])
            ),
            bcc=tuple(
                Address(name=a.get("name"), address=a.get("address"))
                for a in (item.get("bcc") or [])
            ),
            subject=item.get("subject"),
            body_text=item.get("text"),
            body_html=html or None,
            attachments=attachments,
            seen=item.get("seen"),
            size=item.get("size"),
            created_at=parse_dt(item.get("createdAt")),
            raw=item,
        )

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        self._request("DELETE", f"/messages/{message_id}", headers=self._auth_headers(mailbox))

    def mark_read(self, mailbox: Mailbox, message_id: str) -> None:
        self._request(
            "PATCH",
            f"/messages/{message_id}",
            json={"seen": True},
            headers=self._auth_headers(mailbox),
        )

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Delete the account. The account id comes from the creation
        response, else from ``GET /me``. A 404 means the mailbox is gone
        and is reported as MailboxExpired."""
        headers = self._auth_headers(mailbox)
        account_id = (mailbox.raw or {}).get("account_id")
        if not account_id:
            account_id = self._request("GET", "/me", headers=headers).json().get("id")
        try:
            self._request("DELETE", f"/accounts/{account_id}", headers=headers)
        except MessageNotFound as exc:
            raise MailboxExpired(f"mail.tm account {account_id} no longer exists") from exc

    def download_attachment(
        self, mailbox: Mailbox, message_id: str, attachment: Attachment
    ) -> bytes:
        """Download via the attachment's ``downloadUrl`` (relative URLs are
        resolved against the API base URL)."""
        url = attachment.download_url
        if not url:
            raise ProviderError("mail.tm attachment has no download URL")
        headers = self._auth_headers(mailbox)
        if url.startswith("http"):
            response = self.http.request("GET", url, headers=headers)
        else:
            response = self._request("GET", url, headers=headers)
        return response.content
