"""mail.td provider — https://docs.mail.td/llms.txt

API summary (as documented):

* Base ``https://api.mail.td``. Everything except ``GET /api/domains``
  requires ``Authorization: Bearer <td_...>`` (a Pro API token from the
  mail.td dashboard). Free and Pro users use the same endpoints.
* ``GET /api/domains`` (public) → ``{domains: [{id, domain, default}]}``.
* ``POST /api/accounts {address, password}`` → 201 ``{id, address}``;
  account paths accept the UUID or the full address.
* ``GET /api/accounts/{id}/messages?page=`` (30 per page);
  ``GET /api/accounts/{id}/messages/{mid}`` (full message with
  ``text_body``, ``html_body`` and ``attachments`` carrying a zero-based
  ``index``); ``DELETE .../messages/{mid}``; ``PUT .../messages/{mid}/read``;
  ``DELETE /api/accounts/{id}``.
* Attachments are downloaded by index:
  ``GET .../messages/{mid}/attachments/{index}``.
* Errors: ``{"error": {code, message}}``; 400/401/403/404/409/410
  (expired)/429. Free 4 req/s, Pro 10 req/s; account creation 1 per 8s.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, register
from ..caps import Cap
from ..errors import AuthenticationError, MailboxExpired, MessageNotFound, ProviderError
from ..models import Attachment, Mailbox, Message
from ..utils import parse_dt, random_local_part, random_password

_PAGE_SIZE = 30


@register("mailtd")
class MailTdProvider(BaseProvider):
    """mail.td provider (https://mail.td).

    Requires an API token: ``TempMailClient("mailtd", api_key="td_...")``
    (get one at https://mail.td/pro). ``create()`` picks a default domain
    from the public domains endpoint and stores the new account's id on the
    Mailbox as its ``token``; the account password is generated unless
    provided.
    """

    name = "mailtd"
    display_name = "mail.td"
    base_url = "https://api.mail.td"
    default_priority = 60
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

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key

    def _auth(self) -> dict[str, str]:
        if not self.api_key:
            raise AuthenticationError(
                "mail.td requires an API token; pass api_key=... "
                "(get one at https://mail.td/pro)"
            )
        return {"Authorization": f"Bearer {self.api_key}"}

    def domains(self) -> list[dict[str, Any]]:
        """List domains (public endpoint, no auth required)."""
        return self._cache_call(
            f"provider:{self.name}:domains",
            lambda: list(self._request("GET", "/api/domains").json().get("domains", [])),
        )

    @staticmethod
    def _account_id(mailbox: Mailbox) -> str:
        return mailbox.token or mailbox.address

    def create(  # type: ignore[override]
        self,
        *,
        domain: str | None = None,
        local_part: str | None = None,
        password: str | None = None,
    ) -> Mailbox:
        """Create an account (requires api_key).

        Without ``domain``, the first domain from ``GET /api/domains`` is
        used (preferring ``default: true``).
        """
        headers = self._auth()
        if domain is None:
            items = [d for d in self.domains() if d.get("domain")]
            items.sort(key=lambda d: not bool(d.get("default")))
            if not items:
                raise ProviderError("mail.td returned no domains")
            domain = items[0]["domain"]
        password = password or random_password()
        local = local_part or random_local_part()
        address = f"{local}@{domain}"
        account = self._request(
            "POST",
            "/api/accounts",
            json={"address": address, "password": password},
            headers=headers,
        ).json()
        return Mailbox(
            provider=self.name,
            address=address,
            token=account.get("id"),
            password=password,
            created_at=parse_dt(account.get("created_at")),
            raw=account,
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

        ``token`` is the account id (UUID); when omitted the full address
        is used in account paths (both are accepted by the API). Requires
        the provider ``api_key``.
        """
        self._auth()
        return Mailbox(
            provider=self.name,
            address=address,
            token=token,
            password=password,
            _provider=self,
        )

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List messages (30 per page, paginated until exhausted)."""
        headers = self._auth()
        base = f"/api/accounts/{self._account_id(mailbox)}/messages"
        page = 1
        out: list[Message] = []
        while True:
            data = self._request("GET", base, params={"page": page}, headers=headers).json()
            items = list(data.get("messages") or [])
            out.extend(self._map_list_item(item) for item in items)
            if len(items) < _PAGE_SIZE:
                break
            page += 1
        return out

    @staticmethod
    def _map_list_item(item: dict[str, Any]) -> Message:
        return Message(
            id=str(item["id"]),
            sender=item.get("from") or None,
            sender_name=item.get("sender") or None,
            subject=item.get("subject"),
            seen=item.get("is_read"),
            size=item.get("size"),
            created_at=parse_dt(item.get("created_at")),
            raw=item,
        )

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a full message (text/html bodies and attachments)."""
        headers = self._auth()
        data = self._request(
            "GET",
            f"/api/accounts/{self._account_id(mailbox)}/messages/{message_id}",
            headers=headers,
        ).json()
        attachments = tuple(
            Attachment(
                filename=a.get("filename") or "",
                content_type=a.get("content_type"),
                size=a.get("size"),
                index=a.get("index"),
            )
            for a in (data.get("attachments") or [])
        )
        return Message(
            id=str(data["id"]),
            sender=data.get("from") or None,
            sender_name=data.get("sender") or None,
            subject=data.get("subject"),
            body_text=data.get("text_body"),
            body_html=data.get("html_body"),
            attachments=attachments,
            seen=data.get("is_read"),
            size=data.get("size"),
            created_at=parse_dt(data.get("created_at")),
            raw=data,
        )

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/accounts/{self._account_id(mailbox)}/messages/{message_id}",
            headers=self._auth(),
        )

    def mark_read(self, mailbox: Mailbox, message_id: str) -> None:
        self._request(
            "PUT",
            f"/api/accounts/{self._account_id(mailbox)}/messages/{message_id}/read",
            headers=self._auth(),
        )

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Delete the account. A 404 means the mailbox is gone and is
        reported as MailboxExpired."""
        try:
            self._request(
                "DELETE",
                f"/api/accounts/{self._account_id(mailbox)}",
                headers=self._auth(),
            )
        except MessageNotFound as exc:
            raise MailboxExpired(f"mail.td account {mailbox.address} no longer exists") from exc

    def download_attachment(
        self, mailbox: Mailbox, message_id: str, attachment: Attachment
    ) -> bytes:
        """Download by zero-based attachment index."""
        if attachment.index is None:
            raise ProviderError(
                "mail.td attachments are downloaded by index; re-fetch the message first"
            )
        response = self._request(
            "GET",
            f"/api/accounts/{self._account_id(mailbox)}/messages/{message_id}"
            f"/attachments/{attachment.index}",
            headers=self._auth(),
        )
        return response.content
