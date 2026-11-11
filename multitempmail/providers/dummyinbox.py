"""dummyinbox.com provider — https://dummyinbox.com/api

API summary (as documented):

* No key, no signup. Every endpoint lives under
  ``https://dummyinbox.com/api/v1``.
* ``GET /api/v1/domains`` → ``{"domains": [...]}``. Every listed domain
  behaves identically, and an address at any of them already exists — the
  inbox is implicit, exactly like catchmail.io.
* ``POST /api/v1/inboxes {"domain"?: str}`` → 201
  ``{"address", "domain", "webUrl"}``. The server picks a hard-to-guess
  local part; ``domain`` defaults to the first listed domain.
* ``GET /api/v1/inboxes/{address}/messages?limit=&skip=&since=&wait=`` →
  ``{"messages": [...], "total": n}``, newest first, summaries only.
  ``wait`` (0-30s) is a server-side long-poll that returns as soon as a
  message arrives.
* ``GET /api/v1/inboxes/{address}/messages/{id}`` → ``{"message": {...}}``
  with ``text``, ``html``, ``attachments`` and the raw source.

Limits (published): 60 requests/minute per IP, 10 concurrent waits per IP,
messages are deleted after 48 hours, and the API is receive-only — it
cannot send, delete or mutate a message. Inboxes are public: anyone who
knows an address can read it.

No deletion, mark-read or single-address attachment download endpoint is
documented, so those capabilities are not declared.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import BaseProvider, register
from ..caps import Cap
from ..models import Address, Attachment, Mailbox, Message
from ..utils import parse_dt

#: the API caps a single ``wait`` at 30 seconds
_WAIT_MAX_SERVER_SECONDS = 30


@register("dummyinbox")
class DummyInboxProvider(BaseProvider):
    """dummyinbox.com provider — keyless, multi-domain, implicit inboxes.

    The strongest keyless option after catchmail for domain rotation: four
    public domains, each selectable per ``create()`` call, a 60 req/min
    budget, and a real server-side long-poll. The trade-off is short
    retention (48 hours) and no message deletion.
    """

    name = "dummyinbox"
    display_name = "DummyInbox"
    base_url = "https://dummyinbox.com/api/v1"
    # Four selectable domains, the loosest published rate limit (60/min) and a
    # server-side wait. Sits just below catchmail because every create() is a
    # network round-trip and messages vanish after 48 h.
    default_priority = 95
    #: empty by design — the live list comes from ``GET /domains`` and is
    #: cached per instance in ``_domains_cache``
    known_domains: ClassVar[tuple[str, ...]] = ()
    cache_messages = False
    capabilities = frozenset({Cap.CREATE, Cap.LIST, Cap.READ, Cap.SERVER_WAIT, Cap.DOMAIN_SELECT})

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._domains_cache: list[str] | None = None

    # -- domains -----------------------------------------------------------

    def domains(self) -> list[dict[str, Any]]:
        """List usable domains (``GET /domains``), cached per instance."""
        if self._domains_cache is None:
            data = self._request("GET", "/domains").json()
            names = data.get("domains") if isinstance(data, dict) else None
            self._domains_cache = [str(d) for d in (names or []) if d]
        return [{"domain": d, "isActive": True, "isPrivate": False} for d in self._domains_cache]

    def _known(self) -> tuple[str, ...]:
        if self.known_domains:
            return self.known_domains
        return tuple(self._domains_cache or ())

    # -- create ------------------------------------------------------------

    def create(  # type: ignore[override]
        self,
        *,
        domain: str | None = None,
        local_part: str | None = None,
        **_ignored: Any,
    ) -> Mailbox:
        """Create an inbox and return it.

        Args:
            domain: one of the domains from :meth:`domains`; defaults to the
                first. A domain outside that list raises ``ValueError`` naming
                the accepted values instead of returning an address the server
                would silently reject.
            local_part: not supported by this API — the server always picks a
                random local part. Passing one raises ``ValueError`` rather
                than being ignored silently.
        """
        if local_part is not None:
            raise ValueError(
                "dummyinbox.com generates the local part server-side; "
                "local_part is not supported"
            )

        available = tuple(self.domains_names())
        chosen = domain
        if chosen is not None:
            norm = str(chosen).strip().lower()
            if norm not in available:
                raise ValueError(
                    f"unsupported domain {chosen!r}: allowed domains are " f"{', '.join(available)}"
                )
            chosen = norm

        payload: dict[str, Any] = {}
        if chosen:
            payload["domain"] = chosen
        data = self._request("POST", "/inboxes", json=payload).json()

        address = str(data.get("address") or "")
        if not address:
            raise ValueError(f"dummyinbox.com returned no address: {data!r}")
        return Mailbox(
            provider=self.name,
            address=address,
            raw=data,
            _provider=self,
        )

    def domains_names(self) -> list[str]:
        """Just the domain strings, in the order the server lists them."""
        return [d["domain"] for d in self.domains()]

    def attach(self, address: str, **extra: Any) -> Mailbox:  # type: ignore[override]
        """Attach to any address at one of the service's domains."""
        return Mailbox(provider=self.name, address=address, _provider=self)

    # -- read --------------------------------------------------------------

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List message summaries, newest first."""
        data = self._request("GET", f"/inboxes/{mailbox.address}/messages").json()
        return [self._map_summary(item) for item in (data.get("messages") or [])]

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch one full message (bodies, attachments, raw source)."""
        data = self._request("GET", f"/inboxes/{mailbox.address}/messages/{message_id}").json()
        return self._map_full(data.get("message") or {})

    def wait(self, mailbox: Mailbox, timeout: float = 30.0) -> list[Message]:
        """Server-side long-poll; the API holds the request up to 30 seconds.

        The HTTP timeout must outlast the server-side wait, otherwise the
        client aborts a request the server was about to answer.
        """
        seconds = max(1, min(int(timeout) or _WAIT_MAX_SERVER_SECONDS, _WAIT_MAX_SERVER_SECONDS))
        data = self._request(
            "GET",
            f"/inboxes/{mailbox.address}/messages",
            params={"wait": seconds},
            timeout=seconds + 10,
        ).json()
        return [self._map_summary(item) for item in (data.get("messages") or [])]

    # -- mapping -----------------------------------------------------------

    @staticmethod
    def _addresses(value: Any) -> tuple[Address, ...]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return ()
        return tuple(Address(address=str(v)) for v in value if v)

    def _map_summary(self, item: dict[str, Any]) -> Message:
        return Message(
            id=str(item.get("id") or ""),
            sender=item.get("from"),
            sender_name=item.get("fromName"),
            to=self._addresses(item.get("to")),
            cc=self._addresses(item.get("cc")),
            subject=item.get("subject"),
            body_text=item.get("snippet"),
            created_at=parse_dt(item.get("receivedAt")),
            size=None,
            raw=item,
        )

    def _map_full(self, item: dict[str, Any]) -> Message:
        attachments = tuple(
            Attachment(
                id=str(a.get("filename") or "") or None,
                filename=str(a.get("filename") or ""),
                content_type=a.get("contentType"),
                size=a.get("size"),
                # The API inlines the payload; there is no download endpoint.
                index=None,
                download_url=None,
            )
            for a in (item.get("attachments") or [])
            if isinstance(a, dict)
        )
        return Message(
            id=str(item.get("id") or ""),
            sender=item.get("from"),
            sender_name=item.get("fromName"),
            to=self._addresses(item.get("to")),
            cc=self._addresses(item.get("cc")),
            subject=item.get("subject"),
            body_text=item.get("text"),
            body_html=item.get("html"),
            attachments=attachments,
            created_at=parse_dt(item.get("receivedAt")),
            size=None,
            raw=item,
        )
