"""Data models: Mailbox, Message, Attachment, Address."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .caps import Cap
from .errors import MessageNotFound, TempMailError, TimeoutError, WaitCancelled
from .utils import extract_link, extract_otp, substring_match

if TYPE_CHECKING:
    from .base import BaseProvider

__all__ = ["Address", "Attachment", "Mailbox", "Message"]


@dataclass(frozen=True)
class Address:
    """A named email address (as found in To/Cc/Bcc/From headers)."""

    name: str | None = None
    address: str = ""


@dataclass(frozen=True)
class Attachment:
    """An email attachment.

    Providers expose either ``download_url`` (relative or absolute, e.g.
    mail.tm / catchmail.io) or a zero-based ``index`` used to download
    (tempmail.lol). Fetch the content with
    ``mailbox.download(message, attachment)``.
    """

    id: str | None = None
    filename: str = ""
    content_type: str | None = None
    size: int | None = None
    index: int | None = None
    download_url: str | None = None


@dataclass(frozen=True)
class Message:
    """A normalized email message.

    ``body_text``/``body_html`` may be ``None`` when only a summary was
    fetched (mail.tm / catchmail.io list messages without bodies). Call
    ``mailbox.message(id)`` for the full message, or
    use the ``wait_for_*`` helpers which fetch bodies automatically.
    """

    id: str
    sender: str | None = None
    sender_name: str | None = None
    to: tuple[Address, ...] = ()
    cc: tuple[Address, ...] = ()
    bcc: tuple[Address, ...] = ()
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    attachments: tuple[Attachment, ...] = ()
    created_at: datetime | None = None
    seen: bool | None = None
    size: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Mailbox:
    """A temporary email mailbox bound to a provider.

    Created by ``TempMailClient.create()`` (or ``provider.create()``);
    ``TempMailClient.mailbox(address=...)`` attaches to an existing one.

    Attributes:
        provider: provider name (e.g. ``"mailtm"``).
        address: full mailbox address.
        token: provider-specific credential (Bearer token for mail.tm,
            inbox token for tempmail.lol).
        password: account password (mail.tm), needed to
            re-authenticate mail.tm mailboxes.
        expires_at: when the provider deletes the mailbox (if documented).
        created_at: when the mailbox was created (if documented).
        raw: original provider payload.
    """

    provider: str
    address: str
    token: str | None = None
    password: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    _provider: Any = field(default=None, repr=False, compare=False)

    #: default timeout for message-related waits, seconds
    DEFAULT_MESSAGE_TIMEOUT = 60.0
    #: default timeout for OTP/link waits, seconds
    DEFAULT_OTP_TIMEOUT = 120.0
    #: default polling interval for providers without server-side waiting
    DEFAULT_INTERVAL = 2.0

    @property
    def expired(self) -> bool:
        """True when ``expires_at`` is documented and has passed."""
        return self.expires_at is not None and self.expires_at < datetime.now(timezone.utc)

    def _bind(self) -> BaseProvider:
        provider = self._provider
        if provider is None:
            raise TempMailError(
                "mailbox is not attached to a provider; create it via "
                "client.create() or client.mailbox(...)"
            )
        return provider

    def supports(self, capability: str) -> bool:
        """Whether the backing provider supports ``capability`` (see Cap)."""
        return self._bind().supports(capability)

    # -- cache ------------------------------------------------------------

    def _messages_cache_key(self) -> str:
        return f"mailbox:{self.provider}:{self.address}:messages"

    def _invalidate_caches(self) -> None:
        """Drop cached data for this mailbox after a write operation."""
        cache = getattr(self._bind(), "cache", None)
        if cache is not None:
            cache.invalidate_prefix(f"mailbox:{self.provider}:{self.address}:")

    # -- basic operations -------------------------------------------------

    def messages(self) -> list[Message]:
        """List messages in the mailbox (summary fields only for some
        providers; fetch a full body with :meth:`message`).

        When the provider has a cache enabled, the list is memoized with
        the cache TTL; write operations (:meth:`delete`,
        :meth:`mark_read`, :meth:`delete_mailbox`) invalidate it.
        """
        provider = self._bind()
        cache = getattr(provider, "cache", None)
        if cache is not None and getattr(provider, "cache_messages", True):
            key = self._messages_cache_key()
            hit = cache.get(key)
            if hit is not None:
                return list(hit)
            messages = provider.messages(self)
            cache.set(key, messages)
            return messages
        return provider.messages(self)

    def message(self, message_id: str) -> Message:
        """Fetch a single full message by id."""
        return self._bind().message(self, message_id)

    def delete(self, message_id: str) -> None:
        """Delete a single message."""
        self._bind().delete_message(self, message_id)
        self._invalidate_caches()

    def mark_read(self, message_id: str) -> None:
        """Mark a single message as read."""
        self._bind().mark_read(self, message_id)
        self._invalidate_caches()

    def delete_mailbox(self) -> None:
        """Delete the mailbox."""
        self._bind().delete_mailbox(self)
        self._invalidate_caches()

    def download(self, message: Message, attachment: Attachment | int) -> bytes:
        """Download an attachment.

        ``attachment`` is an :class:`Attachment` from ``message.attachments``
        or an integer index into that list.
        """
        provider = self._bind()
        if isinstance(attachment, int):
            if not message.attachments:
                raise TempMailError("message has no attachments")
            try:
                target = message.attachments[attachment]
            except IndexError as exc:
                raise TempMailError(
                    f"attachment index {attachment} out of range "
                    f"(message has {len(message.attachments)})"
                ) from exc
        else:
            target = attachment
        return provider.download_attachment(self, message.id, target)

    # -- waiting ----------------------------------------------------------

    def _ensure_body(self, message: Message) -> Message:
        """Fetch the full message when the summary lacks a body."""
        provider = self._bind()
        if message.body_text is None and message.body_html is None and provider.supports(Cap.READ):
            try:
                return provider.message(self, message.id)
            except MessageNotFound:
                return message
        return message

    @staticmethod
    def _raise_if_cancelled(
        stop: Callable[[], bool] | None, timeout: float, remaining: float
    ) -> None:
        """Raise WaitCancelled when the ``stop`` callback reports True."""
        if stop is not None and stop():
            raise WaitCancelled(f"wait cancelled after {max(timeout - remaining, 0.0):.1f}s")

    def _poll(
        self,
        predicate: Callable[[list[Message]], Any],
        timeout: float,
        interval: float,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> Any:
        """Shared polling loop.

        Uses the provider's server-side long-poll when available
        (``Cap.SERVER_WAIT``); otherwise snapshots the current message ids
        and polls :meth:`messages` until ``predicate`` returns a non-None
        value or ``timeout`` expires. ``stop`` may be a callback returning
        True to cancel early (raises WaitCancelled).

        Note: with server-side waiting providers the first response acts as
        the baseline, so mail already buffered before the first wait may be
        considered "new" (the API cannot distinguish it without a cursor).
        """
        provider = self._bind()
        deadline = time.monotonic() + timeout
        if provider.supports(Cap.SERVER_WAIT):
            while True:
                remaining = deadline - time.monotonic()
                self._raise_if_cancelled(stop, timeout, remaining)
                if remaining <= 0:
                    break
                try:
                    messages = provider.wait(self, timeout=remaining)
                except WaitCancelled:
                    raise
                except TimeoutError:
                    messages = []
                result = predicate(messages)
                if result is not None:
                    return result
        else:
            known = {m.id for m in provider.messages(self)}
            while True:
                remaining = deadline - time.monotonic()
                self._raise_if_cancelled(stop, timeout, remaining)
                if remaining <= 0:
                    break
                messages = [m for m in provider.messages(self) if m.id not in known]
                known.update(m.id for m in messages)
                result = predicate(messages)
                if result is not None:
                    return result
                if remaining > 0:
                    time.sleep(min(interval, remaining))
        raise TimeoutError(f"no matching message arrived within {timeout:.1f}s for {self.address}")

    def wait_for_message(
        self,
        timeout: float = DEFAULT_MESSAGE_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        subject: str | None = None,
        sender: str | None = None,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> Message:
        """Wait for a new message, optionally matching ``subject`` and/or
        ``sender`` (case-insensitive substrings). Only messages that arrive
        after the call are considered. Raises TimeoutError."""
        predicate: Callable[[list[Message]], Message | None] = lambda messages: next(
            (
                m
                for m in messages
                if substring_match(m.subject, subject) and substring_match(m.sender, sender)
            ),
            None,
        )
        return self._poll(predicate, timeout, interval, stop=stop)

    def wait_for_subject(
        self,
        subject: str,
        timeout: float = DEFAULT_MESSAGE_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> Message:
        """Wait for a new message whose subject contains ``subject``."""
        return self.wait_for_message(timeout=timeout, interval=interval, subject=subject, stop=stop)

    def wait_for_sender(
        self,
        sender: str,
        timeout: float = DEFAULT_MESSAGE_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> Message:
        """Wait for a new message whose sender contains ``sender``."""
        return self.wait_for_message(timeout=timeout, interval=interval, sender=sender, stop=stop)

    def wait_for_otp(
        self,
        timeout: float = DEFAULT_OTP_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        pattern: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> str:
        """Wait for a new message containing an OTP and return it.

        ``pattern`` overrides the default OTP patterns (see
        :func:`multitempmail.utils.extract_otp`). ``sender``/``subject`` narrow
        the candidate messages. Raises TimeoutError.
        """

        def predicate(messages: list[Message]) -> str | None:
            for m in messages:
                if not substring_match(m.sender, sender):
                    continue
                if not substring_match(m.subject, subject):
                    continue
                full = self._ensure_body(m)
                for body in (full.body_text, full.body_html):
                    code = extract_otp(body, pattern)
                    if code:
                        return code
            return None

        return self._poll(predicate, timeout, interval, stop=stop)

    def wait_for_link(
        self,
        timeout: float = DEFAULT_OTP_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        keyword: str | None = None,
        domain: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        *,
        filter: Callable[[str], bool] | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> str:
        """Wait for a new message containing a URL and return it.

        ``keyword``/``domain``/``filter`` filter the URLs (see
        :func:`multitempmail.utils.extract_link`), ``sender``/``subject`` narrow
        the candidate messages. Raises TimeoutError.
        """

        def predicate(messages: list[Message]) -> str | None:
            for m in messages:
                if not substring_match(m.sender, sender):
                    continue
                if not substring_match(m.subject, subject):
                    continue
                full = self._ensure_body(m)
                for body in (full.body_text, full.body_html):
                    link = extract_link(body, keyword=keyword, domain=domain, filter=filter)
                    if link:
                        return link
            return None

        return self._poll(predicate, timeout, interval, stop=stop)

    def wait_until(
        self,
        predicate: Callable[[list[Message]], Any],
        timeout: float = DEFAULT_MESSAGE_TIMEOUT,
        interval: float = DEFAULT_INTERVAL,
        *,
        stop: Callable[[], bool] | None = None,
    ) -> Any:
        """Wait until a custom ``predicate(new_messages)`` returns a
        non-None value and return it (return ``None`` to keep waiting).

        Only messages that arrive after the call are passed in. Raises
        TimeoutError, or WaitCancelled when ``stop`` returns True.
        """
        return self._poll(predicate, timeout, interval, stop=stop)
