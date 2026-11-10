"""catchmail.io provider — https://catchmail.io/docs

API summary (as documented):

* No authentication and no mailbox creation — the mailbox is implicit:
  "Use any address @catchmail.io or your own domain". Mail starts
  buffering as soon as it is accepted on the SMTP side.
* ``GET /api/v1/mailbox?address=&page=&page_size=`` → ``{count, messages}``
  with message summaries (no bodies).
* ``GET /api/v1/message/{id}?mailbox=`` → full message with ``body``
  (text/html), headers and attachments (each with a relative
  ``download_url``).
* ``DELETE /api/v1/message/{id}?mailbox=`` → 204.
* Errors: ``{"error": {"code", "message"}}``; 400/404/429/500.
  Rate limit: 1 request/s per IP.

``create()`` only generates a local address (``@catchmail.io`` is the only
documented public domain); it performs no request.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, register
from ..caps import Cap
from ..errors import ProviderError
from ..models import Address, Attachment, Mailbox, Message
from ..utils import parse_dt, random_local_part, validate_domain, validate_local_part


@register("catchmail")
class CatchmailProvider(BaseProvider):
    """catchmail.io provider (https://catchmail.io).

    The mailbox is implicit: ``create()`` returns a locally generated
    address under ``@catchmail.io`` (or a custom ``domain``) without any
    API call. ``delete_mailbox`` is not supported — the API only allows
    deleting individual messages.
    """

    name = "catchmail"
    display_name = "catchmail.io"
    base_url = "https://api.catchmail.io"
    # Highest: no setup, richest key-free capability set, and create() costs
    # no network round-trip. Measured ~5 ms to first usable address.
    default_priority = 100
    known_domains = ("catchmail.io",)
    capabilities = frozenset({Cap.CREATE, Cap.LIST, Cap.READ, Cap.DELETE_MESSAGE, Cap.ATTACHMENTS})

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        #: domains already confirmed by ``expect_delivery`` — one probe each
        self._verified_domains: set[str] = set()

    def create(  # type: ignore[override]
        self,
        *,
        domain: str = "catchmail.io",
        local_part: str | None = None,
        expect_delivery: bool = False,
    ) -> Mailbox:
        """Return a mailbox for a locally generated address.

        No request is made — the address just needs to exist once mail is
        sent to it. Because there is no round-trip, this method validates its
        own inputs: an unchecked ``domain`` would hand back an address the
        server later rejects with an opaque ``validation_error``.

        Args:
            domain: must be a syntactically valid DNS name and one of
                :attr:`known_domains`; anything else raises ``ValueError``
                naming the accepted values.
            local_part: 1-64 chars of letters, digits, ``.``, ``_``, ``%``,
                ``+``, ``-``. An embedded ``@``, a space, a leading/trailing
                separator or an over-long value raises ``ValueError``.
                Empty/``None`` generates a random one.
            expect_delivery: when ``True``, once per provider instance, make a
                single ``GET /api/v1/mailbox`` call so a domain the server does
                not accept fails here instead of at the first ``messages()``.

        Raises:
            ValueError: invalid or unsupported ``domain`` / ``local_part``.
            ProviderError: ``expect_delivery`` was set and the server rejected
                the domain.
        """
        domain = validate_domain(domain, self.known_domains)
        local = validate_local_part(local_part) if local_part else random_local_part()
        address = f"{local}@{domain}"
        mailbox = Mailbox(provider=self.name, address=address, _provider=self)
        if expect_delivery:
            self._verify_domain_accepts(domain, mailbox)
        return mailbox

    def _verify_domain_accepts(self, domain: str, mailbox: Mailbox) -> None:
        """Probe the mailbox endpoint once per domain so a bad domain fails early."""
        if domain in self._verified_domains:
            return
        try:
            self.messages(mailbox)
        except ProviderError as exc:
            raise ProviderError(
                f"catchmail.io rejected domain {domain!r}. Only "
                f"{', '.join(self.known_domains)} is provisioned for public use; "
                f"add your own domain on catchmail.io first. Server said: {exc}"
            ) from exc
        self._verified_domains.add(domain)

    def attach(self, address: str, **extra: Any) -> Mailbox:  # type: ignore[override]
        """Attach to any address (no credentials needed)."""
        return Mailbox(provider=self.name, address=address, _provider=self)

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List message summaries for the mailbox address."""
        data = self._request("GET", "/api/v1/mailbox", params={"address": mailbox.address}).json()
        return [self._map_list_item(item) for item in (data.get("messages") or [])]

    @staticmethod
    def _map_list_item(item: dict[str, Any]) -> Message:
        return Message(
            id=str(item["id"]),
            sender=item.get("from") or None,
            subject=item.get("subject"),
            size=item.get("size"),
            created_at=parse_dt(item.get("date")),
            raw=item,
        )

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a full message (text/html body, headers, attachments)."""
        data = self._request(
            "GET",
            f"/api/v1/message/{message_id}",
            params={"mailbox": mailbox.address},
        ).json()
        body = data.get("body") or {}
        attachments = tuple(
            Attachment(
                id=str(a.get("id")) if a.get("id") is not None else None,
                filename=a.get("filename") or "",
                content_type=a.get("content_type"),
                size=a.get("size"),
                download_url=a.get("download_url"),
            )
            for a in (data.get("attachments") or [])
        )
        return Message(
            id=str(data["id"]),
            sender=data.get("from") or None,
            to=tuple(Address(address=address) for address in (data.get("to") or [])),
            subject=data.get("subject"),
            body_text=(body.get("text") or None),
            body_html=(body.get("html") or None),
            attachments=attachments,
            size=data.get("size"),
            created_at=parse_dt(data.get("date")),
            raw=data,
        )

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v1/message/{message_id}",
            params={"mailbox": mailbox.address},
        )

    def download_attachment(
        self, mailbox: Mailbox, message_id: str, attachment: Attachment
    ) -> bytes:
        """Download via the attachment's ``download_url`` (relative URLs
        are resolved against the API base URL)."""
        url = attachment.download_url
        if not url:
            raise ProviderError("catchmail.io attachment has no download URL")
        if url.startswith("http"):
            response = self.http.request("GET", url)
        else:
            response = self._request("GET", url)
        return response.content
