"""Optional in-memory response cache.

A small TTL key/value store used to avoid re-fetching slowly changing data
(message lists, domain lists). Entries expire on read; write operations
invalidate the affected keys so stale mailbox state is never returned after
a mutation.
"""

from __future__ import annotations

import threading
import time
from typing import Any

__all__ = ["ResponseCache"]


def _key(key: Any) -> str:
    """Normalize any cache key (str, tuple, ...) to a string."""
    return key if isinstance(key, str) else repr(key)


class ResponseCache:
    """Thread-safe, TTL-based in-memory cache.

    Args:
        default_ttl: default lifetime of an entry in seconds.

    Keys may be strings or tuples (e.g. ``("messages", provider, address)``);
    ``invalidate_prefix`` clears every key starting with the given string,
    which lets mailbox write operations purge all entries of one mailbox.
    """

    def __init__(self, default_ttl: float = 30.0) -> None:
        self.default_ttl = default_ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: Any) -> Any:
        """Return the cached value, or ``None`` when missing or expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(_key(key))
            if entry is None:
                return None
            expires, value = entry
            if now >= expires:
                del self._store[_key(key)]
                return None
            return value

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        """Store ``value`` under ``key`` for ``ttl`` (default: the cache's
        ``default_ttl``)."""
        lifetime = self.default_ttl if ttl is None else ttl
        with self._lock:
            self._store[_key(key)] = (time.monotonic() + lifetime, value)

    def invalidate(self, key: Any) -> bool:
        """Remove one entry; returns True when it existed."""
        with self._lock:
            return self._store.pop(_key(key), None) is not None

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove every entry whose key starts with ``prefix``; returns the
        number of removed entries."""
        removed = 0
        with self._lock:
            for key in [k for k in self._store if k.startswith(prefix)]:
                del self._store[key]
                removed += 1
        return removed

    def clear(self) -> None:
        """Remove every entry."""
        with self._lock:
            self._store.clear()

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    @property
    def size(self) -> int:
        """Number of live entries (expired entries are pruned on access)."""
        with self._lock:
            return len(self._store)

    @property
    def keys(self) -> list[str]:
        """All stored keys, for inspection and debugging."""
        with self._lock:
            return sorted(self._store)
