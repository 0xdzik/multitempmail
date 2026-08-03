"""tempmail.lol provider — https://api.tempmail.lol (v3 API)

API summary (as documented):

* ``POST /v3/inboxes {prefix?, domain?, community?}`` → 201 with
  ``{address, token, created_at, expires_at (unix ms), expired,
  webhook_configured}``. Inbox lifetimes: 1h (free), 10h (Plus),
  30h (Ultra).
* ``GET /v3/inboxes/{token}/emails`` — the returned emails are consumed
  (deleted server-side).
* ``GET /v3/inboxes/{token}/wait?timeout=&from=`` — server-side long-poll
  (max 60s, default 30s); the server answers HTTP 200 with
  ``{emails, timed_out, waited_ms}`` even on timeout.
* ``DELETE /v3/inboxes/{token}/emails/{id}``, ``DELETE /v3/inboxes/{token}``.
* Errors: ``{"error": {"code", "message", "required_scope?"}}``.

Not implemented: single-message fetch, mark-read, attachments (no
documented v3 endpoints) — these raise UnsupportedFeature. An optional
Bearer ``api_key`` (Plus/Ultra) is supported but not required.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, register
from ..caps import Cap
from ..errors import AuthenticationError
from ..models import Mailbox, Message
from ..utils import parse_dt

_WAIT_MAX_SERVER_SECONDS = 60


@register("tempmail_lol")
class TempmailLolProvider(BaseProvider):
    """tempmail.lol provider (v3 API, https://tempmail.lol).

    Mailboxes are real inboxes created server-side; the returned ``token``
    identifies the inbox and is required for every operation. Fetching
    emails consumes them server-side, which also makes the ``wait_*``
    helpers naturally incremental. ``expires_at`` is parsed from the
    creation response.
    """

    name = "tempmail_lol"
    display_name = "tempmail.lol"
    base_url = "https://api.tempmail.lol"
    default_priority = 85
    cache_messages = False
    capabilities = frozenset(
        {Cap.CREATE, Cap.LIST, Cap.DELETE_MESSAGE, Cap.DELETE_MAILBOX, Cap.SERVER_WAIT}
    )

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _require_token(self, mailbox: Mailbox) -> dict[str, str]:
        if not mailbox.token:
            raise AuthenticationError(
                "tempmail.lol requires the inbox token; it is returned by "
                "create() — attach with token=..."
            )
        return self._headers()

    def create(  # type: ignore[override]
        self,
        *,
        prefix: str | None = None,
        domain: str | None = None,
        community: bool | None = None,
    ) -> Mailbox:
        """Create an inbox and return the Mailbox with its token."""
        payload: dict[str, Any] = {}
        if prefix:
            payload["prefix"] = prefix
        if domain:
            payload["domain"] = domain
        if community:
            payload["community"] = True
        data = self._request("POST", "/v3/inboxes", json=payload, headers=self._headers()).json()
        return Mailbox(
            provider=self.name,
            address=data["address"],
            token=data.get("token"),
            created_at=parse_dt(data.get("created_at")),
            expires_at=parse_dt(data.get("expires_at")),
            raw=data,
            _provider=self,
        )

    def attach(self, address: str, token: str | None = None, **extra: Any) -> Mailbox:  # type: ignore[override]
        """Attach to an existing inbox (token required)."""
        if not token:
            raise AuthenticationError("tempmail.lol requires the inbox token to access a mailbox")
        return Mailbox(provider=self.name, address=address, token=token, _provider=self)

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """Fetch emails. The returned emails are consumed server-side.

        There is no single-message endpoint in the v3 API, so summaries
        include the full body.
        """
        data = self._request(
            "GET",
            f"/v3/inboxes/{mailbox.token}/emails",
            headers=self._require_token(mailbox),
        ).json()
        return [self._map_item(item) for item in (data.get("emails") or [])]

    def wait(self, mailbox: Mailbox, timeout: float = 30.0) -> list[Message]:
        """Server-side long-poll (``GET /v3/inboxes/{token}/wait``).

        The server waits up to ``min(timeout, 60)`` seconds and always
        answers HTTP 200; ``timed_out`` signals an empty result.
        """
        timeout_s = max(1, min(int(timeout), _WAIT_MAX_SERVER_SECONDS))
        data = self._request(
            "GET",
            f"/v3/inboxes/{mailbox.token}/wait",
            params={"timeout": timeout_s},
            headers=self._require_token(mailbox),
            timeout=timeout_s + 10,
        ).json()
        return [self._map_item(item) for item in (data.get("emails") or [])]

    @staticmethod
    def _map_item(item: dict[str, Any]) -> Message:
        return Message(
            id=str(item["id"]),
            sender=item.get("from") or None,
            subject=item.get("subject"),
            body_text=item.get("body") or item.get("raw"),
            raw=item,
        )

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        self._request(
            "DELETE",
            f"/v3/inboxes/{mailbox.token}/emails/{message_id}",
            headers=self._require_token(mailbox),
        )

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Delete the inbox and all of its emails."""
        self._request(
            "DELETE",
            f"/v3/inboxes/{mailbox.token}",
            headers=self._require_token(mailbox),
        )
