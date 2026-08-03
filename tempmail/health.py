"""Provider health tracking: availability, timing and temporary demotion.

The :class:`HealthManager` observes successes and failures per provider and
derives a temporary *effective priority* used by the failover pool so that
providers that repeatedly fail are tried last (until they recover with a
successful request).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

__all__ = ["HealthManager", "HealthSnapshot"]

#: callback signature consumed by :class:`tempmail.http.HTTPClient` —
#: ``(success, elapsed_seconds)``
HealthCallback = Callable[[bool, float], None]

_MAX_RESPONSE_SAMPLES = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_requests: int = 0
    response_times: list[float] = field(default_factory=list)
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class HealthSnapshot:
    """Point-in-time view of a provider's health.

    Attributes:
        name: provider name.
        healthy: no recent failure streak (``consecutive_failures`` is
            below the ``unhealthy_after`` threshold).
        consecutive_failures / consecutive_successes: streaks in the
            current direction.
        total_requests: observed requests.
        avg_response_time: exponential window average of response times.
        last_success_at / last_failure_at: timestamps of the last events.
        last_error: message of the most recent failure, if any.
        priority: configured priority.
        effective_priority: priority after the health penalty is applied
            (the value used to order provider attempts).
    """

    name: str
    healthy: bool
    consecutive_failures: int
    consecutive_successes: int
    total_requests: int
    avg_response_time: float | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    priority: int
    effective_priority: int


class HealthManager:
    """Tracks per-provider health and derives effective priorities.

    A provider becomes *unhealthy* after ``unhealthy_after`` consecutive
    failures; while unhealthy its effective priority is reduced by
    ``penalty`` per failure (capped at ``4 * penalty``). Any successful
    request resets the failure streak, so providers recover automatically.
    """

    def __init__(self, *, unhealthy_after: int = 2, penalty: int = 20) -> None:
        self.unhealthy_after = unhealthy_after
        self.penalty = penalty
        self._states: dict[str, _ProviderState] = {}

    def record_success(self, name: str, elapsed: float = 0.0) -> None:
        """Record a successful request for ``name``."""
        state = self._states.setdefault(name, _ProviderState())
        state.consecutive_failures = 0
        state.consecutive_successes += 1
        state.total_requests += 1
        if elapsed > 0:
            state.response_times.append(elapsed)
            if len(state.response_times) > _MAX_RESPONSE_SAMPLES:
                del state.response_times[: len(state.response_times) - _MAX_RESPONSE_SAMPLES]
        state.last_success_at = _utcnow()

    def record_failure(self, name: str, error: str | None = None) -> None:
        """Record a failed request for ``name``."""
        state = self._states.setdefault(name, _ProviderState())
        state.consecutive_failures += 1
        state.consecutive_successes = 0
        state.total_requests += 1
        state.last_failure_at = _utcnow()
        if error:
            state.last_error = error

    def reset(self, name: str) -> None:
        """Forget everything tracked for ``name``."""
        self._states.pop(name, None)

    def clear(self) -> None:
        """Forget all tracked state."""
        self._states.clear()

    def is_healthy(self, name: str) -> bool:
        """Whether ``name`` has no failure streak at or above the threshold."""
        state = self._states.get(name)
        return state is None or state.consecutive_failures < self.unhealthy_after

    def effective_priority(self, name: str, base_priority: int) -> int:
        """Priority adjusted for health: every failure in the current
        streak costs ``penalty``, capped at ``4 * penalty``. Healthy
        providers keep their configured priority."""
        state = self._states.get(name)
        if state is None or state.consecutive_failures == 0:
            return base_priority
        steps = min(state.consecutive_failures, 4)
        return max(base_priority - self.penalty * steps, 0)

    def snapshot(self, name: str, priority: int) -> HealthSnapshot:
        """Health view for ``name`` (zeros for providers never observed)."""
        state = self._states.get(name)
        if state is None:
            return HealthSnapshot(
                name=name,
                healthy=True,
                consecutive_failures=0,
                consecutive_successes=0,
                total_requests=0,
                avg_response_time=None,
                last_success_at=None,
                last_failure_at=None,
                last_error=None,
                priority=priority,
                effective_priority=priority,
            )
        times = state.response_times
        return HealthSnapshot(
            name=name,
            healthy=self.is_healthy(name),
            consecutive_failures=state.consecutive_failures,
            consecutive_successes=state.consecutive_successes,
            total_requests=state.total_requests,
            avg_response_time=fmean(times) if times else None,
            last_success_at=state.last_success_at,
            last_failure_at=state.last_failure_at,
            last_error=state.last_error,
            priority=priority,
            effective_priority=self.effective_priority(name, priority),
        )

    def snapshots(self, priorities: dict[str, int] | None = None) -> list[HealthSnapshot]:
        """Health views for every tracked provider (plus ``priorities``
        keys, used to look up configured priorities)."""
        names = sorted(set(self._states) | set((priorities or {}).keys()))
        return [self.snapshot(name, (priorities or {}).get(name, 0)) for name in names]

    @property
    def providers(self) -> list[str]:
        """Names of all providers with recorded activity."""
        return sorted(self._states)

    def __contains__(self, name: Any) -> bool:
        return name in self._states
