"""Exception hierarchy for the tempmail SDK.

All provider-specific failures are raised as :class:`TempMailError`
subclasses so callers can catch a single base type. HTTP failures are
mapped from status codes by :func:`error_from_response`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AuthenticationError",
    "MailboxExpired",
    "MessageNotFound",
    "ProviderError",
    "RateLimitError",
    "TempMailError",
    "TimeoutError",
    "UnsupportedFeature",
    "WaitCancelled",
    "error_from_response",
    "extract_error_message",
]


class TempMailError(Exception):
    """Base class for every error raised by the SDK.

    Attributes:
        message: human readable description.
        status_code: HTTP status that caused the error, if any.
        response: the underlying httpx response, if any.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response


class ProviderError(TempMailError):
    """An API call failed in a way not covered by the more specific errors."""


class AuthenticationError(TempMailError):
    """Missing, invalid or revoked credentials (HTTP 401/403)."""


class MailboxExpired(TempMailError):
    """The mailbox no longer exists (HTTP 410, or 404 on mailbox-level
    operations for providers whose mailboxes expire)."""


class RateLimitError(TempMailError):
    """The provider's rate limit was hit (HTTP 429)."""


class TimeoutError(TempMailError):
    """A request timed out, or no matching message arrived within the wait
    window. Note: this class intentionally shadows the builtin
    ``TimeoutError`` within the package namespace."""


class MessageNotFound(TempMailError):
    """The requested message does not exist (HTTP 404 on message-level
    operations)."""


class UnsupportedFeature(TempMailError):
    """The provider does not implement the requested capability."""


class WaitCancelled(TimeoutError):
    """A ``wait_for_*`` helper was cancelled via its ``stop`` callback."""


def _lookup_message(body: Any) -> str | None:
    """Best-effort extraction of a readable error from common error JSON
    shapes: ``{"error": {...}}``, ``{"error": "code"}``, ``{"message": "..."}``."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        parts = [
            str(error.get("code", "")).strip(),
            str(error.get("message", "")).strip(),
        ]
        return ": ".join(part for part in parts if part) or None
    if isinstance(error, str) and error:
        return f"error {error}"
    message = body.get("message")
    return str(message) if message else None


def extract_error_message(response: Any) -> str:
    """Human readable error message extracted from an httpx response."""
    text = (response.text or "").strip()
    try:
        body = response.json()
    except ValueError:
        body = None
    message = _lookup_message(body) if isinstance(body, dict) else None
    if not message and text:
        message = text[:300]
    return f"HTTP {response.status_code}" + (f": {message}" if message else "")


def error_from_response(response: Any) -> TempMailError:
    """Map an httpx response with status >= 400 to a ``TempMailError`` subclass.

    Mapping rules::

        401/403 -> AuthenticationError
        404     -> MessageNotFound
        408     -> TimeoutError
        410     -> MailboxExpired
        429     -> RateLimitError
        other   -> ProviderError

    Providers may override the 404 mapping for mailbox-level operations
    (see e.g. ``MailTmProvider.delete_mailbox``).
    """
    status = response.status_code
    message = extract_error_message(response)
    if status in (401, 403):
        return AuthenticationError(message, status_code=status, response=response)
    if status == 404:
        return MessageNotFound(message, status_code=status, response=response)
    if status == 408:
        return TimeoutError(message, status_code=status, response=response)
    if status == 410:
        return MailboxExpired(message, status_code=status, response=response)
    if status == 429:
        return RateLimitError(message, status_code=status, response=response)
    return ProviderError(message, status_code=status, response=response)
