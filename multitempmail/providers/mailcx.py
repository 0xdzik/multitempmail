"""mail.cx provider — https://mail.cx/api-docs/

API summary (as documented):

* Base ``https://api.mail.cx/v1``; optional auth via the ``x-api-token``
  header (``tm_live_...`` tokens). No mailbox creation — the mailbox is
  implicit: mail starts buffering the moment the SMTP gateway accepts it,
  and is kept for 1h before auto-delete.
* ``GET /v1/inbox/{address}`` is a server long-poll: it blocks up to 25
  seconds (server-fixed) and answers 200 with emails newest-first as soon
  as mail arrives, or 204 when the hold expires. ``since`` is an opaque
  cursor taken from the previous response's ``next_since``; ``count``
  (default 1, max 50) and ``limit`` (default 20, max 50) shape the reply.
* ``DELETE /v1/inbox/{address}`` clears every email at the address.
* ``GET /v1/email/{id}`` (full parsed email), ``GET /v1/email/{id}/raw``,
  ``GET /v1/email/{id}/attachments/{i}`` (zero-based index),
  ``DELETE /v1/email/{id}``.
* ``GET /v1/config`` is public and lists ``system_domains`` and
  ``local_part_rules``.
* Errors: ``{"error": "<code>"}``; 400/401/403/404/410 (email_expired)/
  429 (rate_limit_exceeded, token_busy, addr_busy)/440/503.

``create()`` picks a system domain (preferring ``default: true``) from
``/v1/config`` and generates a local part obeying ``local_part_rules``.
Long-poll listing means ``messages()`` can block up to 25 seconds when the
inbox is empty; ``wait()`` uses the ``next_since`` cursor so each wait only
returns strictly newer mail.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, register
from ..caps import Cap
from ..errors import ProviderError
from ..models import Attachment, Mailbox, Message
from ..utils import parse_dt, random_local_part

#: httpx read timeout for the long-polling inbox endpoint (server holds 25s)
_INBOX_TIMEOUT = 40.0


@register("mailcx")
class MailCxProvider(BaseProvider):
    """mail.cx provider (https://mail.cx).

    The mailbox is implicit; ``create()`` performs a single public
    ``GET /v1/config`` to pick a system domain and returns a locally
    generated address. ``delete_mailbox()`` maps to the documented "clear
    every email at the address" endpoint. An optional ``api_token``
    (``tm_live_...``) raises the rate limits.
    """

    name = "mailcx"
    display_name = "mail.cx"
    base_url = "https://api.mail.cx/v1"
    default_priority = 95
    capabilities = frozenset(
        {
            Cap.CREATE,
            Cap.LIST,
            Cap.READ,
            Cap.DELETE_MESSAGE,
            Cap.DELETE_MAILBOX,
            Cap.ATTACHMENTS,
            Cap.SERVER_WAIT,
        }
    )

    def __init__(self, *, api_token: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_token = api_token
        #: next_since cursor per address, set from inbox responses
        self._cursors: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        return {"x-api-token": self.api_token} if self.api_token else {}

    def config(self) -> dict[str, Any]:
        """Server configuration (public endpoint ``GET /v1/config``)."""
        return self._cache_call(
            f"provider:{self.name}:domains",
            lambda: self._request("GET", "/config").json(),
        )

    def system_domains(self) -> list[str]:
        """System domains advertised by the server."""
        return [d["domain"] for d in self.config().get("system_domains", [])]

    def create(  # type: ignore[override]
        self,
        *,
        domain: str | None = None,
        local_part: str | None = None,
    ) -> Mailbox:
        """Return a mailbox for a locally generated address.

        Without ``domain`` the first system domain from ``/v1/config`` is
        used (preferring ``default: true``); the local part follows the
        server's ``local_part_rules`` when present.
        """
        cfg = self.config()
        if domain is None:
            domains = [d for d in cfg.get("system_domains", []) if d.get("domain")]
            domains.sort(key=lambda d: not bool(d.get("default")))
            if not domains:
                raise ProviderError("mail.cx /v1/config returned no system domains")
            domain = domains[0]["domain"]
        rules = cfg.get("local_part_rules") or {}
        local = local_part or random_local_part(
            pattern=rules.get("pattern"),
            max_length=rules.get("max_length"),
        )
        address = f"{local}@{domain}"
        return Mailbox(provider=self.name, address=address, _provider=self)

    def attach(self, address: str, **extra: Any) -> Mailbox:  # type: ignore[override]
        """Attach to any address (no credentials needed)."""
        return Mailbox(provider=self.name, address=address, _provider=self)

    def _map_emails(self, data: dict[str, Any]) -> list[Message]:
        return [
            Message(
                id=str(item["id"]),
                sender=item.get("from_email") or None,
                subject=item.get("subject"),
                created_at=parse_dt(item.get("created_at")),
                raw=item,
            )
            for item in (data.get("emails") or [])
        ]

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List newest messages.

        Note: this endpoint long-polls server-side and may block up to 25
        seconds when nothing is buffered; 204 (nothing new) maps to an
        empty list. Email summaries persist until deleted.
        """
        response = self._request(
            "GET",
            f"/inbox/{mailbox.address}",
            params={"count": 1, "limit": 50},
            headers=self._headers(),
            timeout=_INBOX_TIMEOUT,
        )
        if response.status_code == 204:
            return []
        data = response.json()
        if data.get("next_since"):
            self._cursors[mailbox.address] = data["next_since"]
        return self._map_emails(data)

    def wait(self, mailbox: Mailbox, timeout: float = 30.0) -> list[Message]:
        """Server long-poll for new mail (25s hold per call; the cursor
        from the previous response only returns strictly newer mail)."""
        params: dict[str, Any] = {"count": 1, "limit": 50}
        cursor = self._cursors.get(mailbox.address)
        if cursor:
            params["since"] = cursor
        response = self._request(
            "GET",
            f"/inbox/{mailbox.address}",
            params=params,
            headers=self._headers(),
            timeout=min(_INBOX_TIMEOUT, timeout + 2),
        )
        if response.status_code == 204:
            return []
        data = response.json()
        if data.get("next_since"):
            self._cursors[mailbox.address] = data["next_since"]
        return self._map_emails(data)

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a full parsed email (text + html + attachments).

        The exact field names are not documented, so text/html/attachments
        are extracted defensively.
        """
        data = self._request("GET", f"/email/{message_id}", headers=self._headers()).json()
        body = data.get("body") if isinstance(data.get("body"), dict) else {}
        text = data.get("text") or body.get("text")
        html = data.get("html") or body.get("html")
        attachments = tuple(
            Attachment(
                filename=a.get("filename") or a.get("name") or "",
                content_type=a.get("content_type") or a.get("contentType"),
                size=a.get("size"),
                index=i,
            )
            for i, a in enumerate(data.get("attachments") or [])
        )
        return Message(
            id=str(data.get("id", message_id)),
            sender=data.get("from_email") or data.get("from") or None,
            subject=data.get("subject"),
            body_text=text,
            body_html=html,
            attachments=attachments,
            created_at=parse_dt(data.get("created_at")),
            raw=data,
        )

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        self._request("DELETE", f"/email/{message_id}", headers=self._headers())

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Clear every email at the address (documented behaviour of
        ``DELETE /v1/inbox/{address}``)."""
        self._request("DELETE", f"/inbox/{mailbox.address}", headers=self._headers())

    def download_attachment(
        self, mailbox: Mailbox, message_id: str, attachment: Attachment
    ) -> bytes:
        """Download by zero-based attachment index."""
        if attachment.index is None:
            raise ProviderError(
                "mail.cx attachments are downloaded by index; re-fetch the message first"
            )
        response = self._request(
            "GET",
            f"/email/{message_id}/attachments/{attachment.index}",
            headers=self._headers(),
        )
        return response.content
