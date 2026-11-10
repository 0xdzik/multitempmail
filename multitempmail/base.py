"""Base provider contract, capability system and provider registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from .cache import ResponseCache
from .caps import Cap
from .errors import UnsupportedFeature
from .health import HealthManager
from .http import HTTPClient
from .models import Mailbox, Message

__all__ = [
    "BaseProvider",
    "ProviderInfo",
    "get_aliases",
    "get_provider",
    "list_providers",
    "register",
]


@dataclass(frozen=True)
class ProviderInfo:
    """Static metadata about a registered provider.

    Attributes:
        name: canonical provider name (e.g. ``"mailtm"``).
        display_name: human friendly name (e.g. ``"Mail.tm"``).
        priority: configured selection priority; higher wins.
        capabilities: supported :class:`~multitempmail.caps.Cap` values.
        aliases: accepted names (including ``name`` itself).
        known_domains: statically-known usable domains (empty when the
            provider publishes its domain list at runtime instead).
    """

    name: str
    display_name: str
    priority: int
    capabilities: frozenset[str]
    aliases: tuple[str, ...]
    known_domains: tuple[str, ...]

    def supports(self, capability: str) -> bool:
        """Whether this provider supports ``capability`` (see Cap)."""
        return capability in self.capabilities


class BaseProvider:
    """Contract every provider implements.

    Subclasses declare ``name``, ``display_name``, ``base_url`` and the
    subset of :class:`~multitempmail.caps.Cap` they support. All HTTP goes
    through a single :class:`~multitempmail.http.HTTPClient`; failures are
    mapped to :mod:`multitempmail.errors` subclasses.
    """

    name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base Provider"
    base_url: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[str]] = frozenset()
    #: priority used for provider selection/failover ordering; higher wins.
    #: Subclasses may override; an instance-level ``priority=`` option wins.
    default_priority: ClassVar[int] = 100
    #: domains usable without a runtime lookup (""/None when the provider
    #: publishes its list at runtime, e.g. mail.tm).
    known_domains: ClassVar[tuple[str, ...]] = ()
    #: whether message lists may be cached (False for APIs that consume
    #: emails on read, e.g. tempmail.lol).
    cache_messages: ClassVar[bool] = True

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        proxy: str | None = None,
        user_agent: str | None = None,
        http: HTTPClient | None = None,
        priority: int | None = None,
        cache: ResponseCache | None = None,
        health: HealthManager | None = None,
        **options: Any,
    ) -> None:
        if options:
            unknown = ", ".join(sorted(options))
            raise TypeError(f"{self.name}: unexpected options: {unknown}")
        self.priority = self.default_priority if priority is None else priority
        self.cache = cache
        self.health = health
        self.timeout = timeout
        self.retries = retries
        if http is None:
            http = HTTPClient(
                base_url=self.base_url,
                timeout=timeout,
                retries=retries,
                proxy=proxy,
                user_agent=user_agent,
                metrics=self._http_metrics if health is not None else None,
            )
        self.http = http

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"

    def _http_metrics(self, success: bool, elapsed: float) -> None:
        """Report an HTTP outcome to the health manager (if one is set)."""
        if self.health is None:
            return
        if success:
            self.health.record_success(self.name, elapsed)
        else:
            self.health.record_failure(self.name)

    def info(self) -> ProviderInfo:
        """Static metadata about this provider (see ProviderInfo)."""
        return ProviderInfo(
            name=self.name,
            display_name=self.display_name,
            priority=self.priority,
            capabilities=self.capabilities,
            aliases=get_aliases(self.name),
            known_domains=self.known_domains,
        )

    def _cache_call(self, key: str, fetcher: Any, ttl: float | None = None) -> Any:
        """Evaluate ``fetcher()`` and memoize the result in ``self.cache``
        (no-op when the provider has no cache)."""
        cache = self.cache
        if cache is not None:
            hit = cache.get(key)
            if hit is not None:
                return hit
        value = fetcher()
        if cache is not None:
            cache.set(key, value, ttl)
        return value

    def clear_cache(self) -> None:
        """Drop every cached entry belonging to this provider."""
        if self.cache is not None:
            self.cache.invalidate_prefix(f"provider:{self.name}:")

    # -- capabilities -----------------------------------------------------

    def supports(self, capability: str) -> bool:
        """Whether this provider supports ``capability`` (see Cap)."""
        return capability in self.capabilities

    def require(self, capability: str, hint: str | None = None) -> None:
        """Raise UnsupportedFeature unless ``capability`` is supported."""
        if not self.supports(capability):
            detail = f" ({hint})" if hint else ""
            raise UnsupportedFeature(f"{self.display_name} does not support {capability!r}{detail}")

    # -- operations (overridden by providers) -----------------------------

    def create(self, **kwargs: Any) -> Mailbox:
        """Create a new mailbox (Cap.CREATE)."""
        self.require(Cap.CREATE)
        raise NotImplementedError(f"{self.name} must implement create()")

    def attach(
        self, address: str, token: str | None = None, password: str | None = None, **extra: Any
    ) -> Mailbox:
        """Attach to an existing mailbox without creating anything."""
        raise NotImplementedError(f"{self.name} must implement attach()")

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List messages (Cap.LIST)."""
        self.require(Cap.LIST)
        raise NotImplementedError(f"{self.name} must implement messages()")

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a single full message (Cap.READ)."""
        self.require(Cap.READ)
        raise NotImplementedError(f"{self.name} must implement message()")

    def delete_message(self, mailbox: Mailbox, message_id: str) -> None:
        """Delete a single message (Cap.DELETE_MESSAGE)."""
        self.require(Cap.DELETE_MESSAGE)
        raise NotImplementedError(f"{self.name} must implement delete_message()")

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Delete (or clear) the mailbox (Cap.DELETE_MAILBOX)."""
        self.require(Cap.DELETE_MAILBOX)
        raise NotImplementedError(f"{self.name} must implement delete_mailbox()")

    def mark_read(self, mailbox: Mailbox, message_id: str) -> None:
        """Mark a single message as read (Cap.MARK_READ)."""
        self.require(Cap.MARK_READ)
        raise NotImplementedError(f"{self.name} must implement mark_read()")

    def download_attachment(self, mailbox: Mailbox, message_id: str, attachment: Any) -> bytes:
        """Download an attachment (Cap.ATTACHMENTS)."""
        self.require(Cap.ATTACHMENTS)
        raise NotImplementedError(f"{self.name} must implement download_attachment()")

    def wait(self, mailbox: Mailbox, timeout: float = 30.0) -> list[Message]:
        """Server-side long-poll for new mail (Cap.SERVER_WAIT)."""
        self.require(Cap.SERVER_WAIT)
        raise NotImplementedError(f"{self.name} must implement wait()")

    # -- http helper ------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.http.request(method, path, **kwargs)


# -- registry ---------------------------------------------------------------

_REGISTRY: dict[str, type[BaseProvider]] = {}

_ALIASES = {
    "mailtm": "mailtm",
    "mail.tm": "mailtm",
    "catchmail": "catchmail",
    "catchmail.io": "catchmail",
    "tempmail_lol": "tempmail_lol",
    "tempmail.lol": "tempmail_lol",
    "tempmail": "tempmail_lol",
    "dummyinbox": "dummyinbox",
    "dummyinbox.com": "dummyinbox",
    "dummy": "dummyinbox",
}


def register(name: str):
    """Decorator registering a provider class under ``name``."""

    def decorator(cls: type[BaseProvider]) -> type[BaseProvider]:
        _REGISTRY[name.lower()] = cls
        return cls

    return decorator


def get_provider(name: str, **options: Any) -> BaseProvider:
    """Instantiate the provider registered under ``name``.

    Names are case-insensitive; common aliases (``mail.tm``,
    ``tempmail.lol``) are accepted. Extra keyword arguments are passed to the
    provider constructor.
    """
    key = _ALIASES.get(str(name).lower().strip(), str(name).lower().strip())
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown provider {name!r}; available: {available}")
    return _REGISTRY[key](**options)


def get_aliases(name: str) -> tuple[str, ...]:
    """All names accepted for ``name`` (including ``name`` itself)."""
    key = _ALIASES.get(str(name).lower().strip(), str(name).lower().strip())
    return tuple(sorted(alias for alias, target in _ALIASES.items() if target == key))


def list_providers() -> list[str]:
    """Names of all registered providers, sorted."""
    return sorted(_REGISTRY)
