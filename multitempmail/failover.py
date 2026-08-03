"""Provider failover: try providers in priority order until one succeeds.

Transient failures (rate limit, timeouts, 5xx, transport errors) move the
pool to the next provider and demote the failed one via the health manager.
Permanent failures (invalid parameters, bad credentials) stop the attempt —
they are not retried against other providers.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .errors import (
    AuthenticationError,
    ProviderError,
    RateLimitError,
    TempMailError,
    TimeoutError,
)
from .health import HealthManager

if TYPE_CHECKING:
    from .base import BaseProvider
    from .models import Mailbox

__all__ = ["FailoverError", "ProviderPool", "is_recoverable"]


class FailoverError(TempMailError):
    """Every provider in the pool failed (or a provider returned a
    permanent error worth reporting).

    Attributes:
        failures: list of ``(provider name, exception)`` pairs in the order
            they were attempted.
    """

    def __init__(
        self,
        message: str,
        failures: list[tuple[str, TempMailError]],
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.failures = list(failures)

    def __str__(self) -> str:
        details = "; ".join(f"{name}: {exc}" for name, exc in self.failures)
        return f"{self.message} [{details}]"


def is_recoverable(error: TempMailError) -> bool:
    """Return True when it makes sense to try the *next* provider.

    Rate limits and timeouts are transient; a :class:`ProviderError` with no
    status (transport failure) or a 5xx status is treated as transient.
    Everything else (invalid parameter 4xx, auth failures, missing mailboxes)
    is permanent and should not be retried against other providers.
    """
    if isinstance(error, (RateLimitError, TimeoutError)):
        return True
    if isinstance(error, ProviderError):
        return error.status_code is None or error.status_code >= 500
    return False


class ProviderPool:
    """Attempts a single provider operation across the pool.

    Provider order is derived from the health-adjusted priority, so a
    provider that repeatedly fails is tried last until it recovers.
    """

    def __init__(
        self,
        providers: list[BaseProvider],
        *,
        health: HealthManager | None = None,
    ) -> None:
        self.providers = list(providers)
        self.health = health if health is not None else HealthManager()

    def ordered(self) -> list[BaseProvider]:
        """Providers sorted by effective priority (highest first)."""
        return sorted(
            self.providers,
            key=lambda p: (self.health.effective_priority(p.name, p.priority), p.name),
            reverse=True,
        )

    def create(self, **kwargs: Any) -> Mailbox:
        """Create a mailbox on the first provider that succeeds.

        Recoverable failures advance to the next provider; a permanent
        failure raises :class:`FailoverError` immediately (with the
        providers attempted so far). If every provider fails, a
        :class:`FailoverError` listing all failures is raised.
        """
        failures: list[tuple[str, TempMailError]] = []
        for provider in self.ordered():
            start = time.monotonic()
            try:
                mailbox = provider.create(**kwargs)
            except TempMailError as exc:
                self.health.record_failure(provider.name, str(exc))
                failures.append((provider.name, exc))
                if not is_recoverable(exc):
                    raise FailoverError(
                        f"provider {provider.name} failed with a permanent error", failures
                    ) from exc
                continue
            self.health.record_success(provider.name, time.monotonic() - start)
            return mailbox
        raise FailoverError(
            f"all {len(self.providers)} providers failed to create a mailbox", failures
        )

    def attach(self, address: str, **kwargs: Any) -> Mailbox:
        """Attach on the first provider that accepts the credentials.

        Providers that cannot authenticate (missing/invalid credentials for
        that specific provider, e.g. a mailbox token that belongs to
        another provider) are skipped only for ``AuthenticationError``;
        any other error is raised immediately.
        """
        failures: list[tuple[str, TempMailError]] = []
        for provider in self.ordered():
            try:
                return provider.attach(address, **kwargs)
            except AuthenticationError as exc:
                failures.append((provider.name, exc))
                continue
            except TempMailError:
                raise
        raise FailoverError(
            f"no provider could attach to {address} "
            f"(all raised AuthenticationError: {len(failures)})",
            failures,
        )
