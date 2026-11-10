"""High-level facade over a provider pool and the mailboxes it manages."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from . import session as _session
from .base import BaseProvider, ProviderInfo, get_provider, list_providers
from .failover import ProviderPool
from .health import HealthManager, HealthSnapshot
from .models import Attachment, Mailbox, Message
from .utils import parse_dt

__all__ = ["TempMailClient"]


class TempMailClient:
    """Unified entry point for every provider.

    Args:
        provider: provider name (e.g. ``"mailtm"``), a provider instance,
            an iterable of names/instances — or ``None`` to use every
            registered provider. With several providers, :meth:`create`
            automatically fails over in priority order (see below).
        **options: passed to every provider constructor (e.g.
            ``proxy=`` / ``timeout=`` / ``retries`` for the HTTP layer,
            ``priority=`` / ``cache=`` / ``health=`` for selection).

    Provider selection & failover::

        client = TempMailClient()          # every provider, priority order
        mailbox = client.create()          # first provider that succeeds

    Providers are tried in order of *effective priority* (configured
    priority adjusted by live health). Rate limits, timeouts and transient
    5xx/transport errors advance to the next provider; permanent errors
    (invalid parameters, bad credentials) raise immediately. A provider
    that fails repeatedly is demoted temporarily and recovers after a
    successful request. See :meth:`providers` and :meth:`health`.
    """

    def __init__(
        self,
        provider: str | BaseProvider | Iterable[str | BaseProvider] | None = None,
        **options: Any,
    ) -> None:
        self._init_options = dict(options)
        if "health" not in options:
            options["health"] = HealthManager()
        self._health = options["health"]
        if provider is None:
            names = list_providers()
            providers = [get_provider(name, **options) for name in names]
        elif isinstance(provider, (str, BaseProvider)):
            providers = [
                (
                    provider
                    if isinstance(provider, BaseProvider)
                    else get_provider(provider, **options)
                )
            ]
        else:
            providers = [
                item if isinstance(item, BaseProvider) else get_provider(item, **options)
                for item in provider
            ]
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = sorted(providers, key=lambda p: (p.priority, p.name), reverse=True)
        self.provider = self._providers[0]
        self.pool = ProviderPool(self._providers, health=self._health)
        self._active: Mailbox | None = None

    @property
    def name(self) -> str:
        """Name of the highest-priority provider (what ``create()`` tries
        first), e.g. ``"mailtm"``."""
        return self.pool.ordered()[0].name

    def supports(self, capability: str) -> bool:
        """Whether any provider supports ``capability`` (see Cap)."""
        return any(p.supports(capability) for p in self._providers)

    def providers(self) -> list[ProviderInfo]:
        """Static metadata for every provider in this client, in priority
        order: name, aliases, capabilities, priority and known domains."""
        return [p.info() for p in self._providers]

    def health(self) -> list[HealthSnapshot]:
        """Live health snapshot per provider: availability, failure
        streaks, average response time and effective priority."""
        return [self._health.snapshot(p.name, p.priority) for p in self._providers]

    @property
    def active(self) -> Mailbox | None:
        """The mailbox most recently created or attached by this client."""
        return self._active

    # -- mailbox lifecycle -------------------------------------------------

    def create(self, **kwargs: Any) -> Mailbox:
        """Create a new mailbox; provider-specific options are passed
        through (``domain=``, ``local_part=``, ``password=``, ``prefix=``...).

        With a multi-provider pool, transient failures fail over to the
        next provider until one succeeds (see :class:`~multitempmail.failover`).
        """
        mailbox = self.pool.create(**kwargs)
        self._active = mailbox
        return mailbox

    def mailbox(self, address: str, **kwargs: Any) -> Mailbox:
        """Attach to an existing mailbox (``token=``/``password=`` where the
        provider requires credentials). With a pool, providers that cannot
        authenticate are skipped in priority order."""
        mailbox = self.pool.attach(address, **kwargs)
        self._active = mailbox
        return mailbox

    # -- sessions ---------------------------------------------------------

    def save_session(self, path: str) -> None:
        """Persist the provider pool, client options and the active mailbox
        to a JSON file; restore with :meth:`load_session`."""
        _session.save_session(
            path,
            providers=[p.name for p in self._providers],
            options=self._init_options,
            mailbox=self._active,
        )

    @classmethod
    def load_session(cls, path: str) -> TempMailClient:
        """Recreate a client (and its active mailbox, if saved) from a
        session file written by :meth:`save_session`."""
        payload = _session.load_session(path)
        client = cls(list(payload["providers"]), **payload.get("options") or {})
        mailbox_data = payload.get("mailbox")
        if mailbox_data:
            client._restore_mailbox(mailbox_data)
        return client

    def restore_session(self, path: str) -> None:
        """Replace this client's configuration in place with a saved
        session (see :meth:`save_session`)."""
        restored = self.__class__.load_session(path)
        self._providers = restored._providers
        self.provider = restored.provider
        self.pool = restored.pool
        self._health = restored._health
        self._active = restored._active
        self._init_options = restored._init_options

    def _restore_mailbox(self, data: dict[str, Any]) -> Mailbox:
        provider = next((p for p in self._providers if p.name == data["provider"]), None)
        if provider is None:
            raise ValueError(f"session mailbox provider {data['provider']!r} is not in this client")
        try:
            mailbox = provider.attach(data["address"])
        except Exception:  # noqa: BLE001 - restore must degrade gracefully
            mailbox = None
        if mailbox is not None:
            restored = replace(
                mailbox,
                token=data.get("token"),
                password=data.get("password"),
                expires_at=parse_dt(data.get("expires_at")),
                created_at=parse_dt(data.get("created_at")),
                raw=data.get("raw") or {},
            )
        else:
            restored = Mailbox(
                provider=data["provider"],
                address=data["address"],
                token=data.get("token"),
                password=data.get("password"),
                expires_at=parse_dt(data.get("expires_at")),
                created_at=parse_dt(data.get("created_at")),
                raw=data.get("raw") or {},
                _provider=provider,
            )
        self._active = restored
        return restored

    # -- delegations (convenience wrappers over Mailbox) -------------------

    def messages(self, mailbox: Mailbox) -> list[Message]:
        """List messages in ``mailbox``."""
        return mailbox.messages()

    def message(self, mailbox: Mailbox, message_id: str) -> Message:
        """Fetch a single full message."""
        return mailbox.message(message_id)

    def delete(self, mailbox: Mailbox, message_id: str) -> None:
        """Delete a single message."""
        mailbox.delete(message_id)

    def mark_read(self, mailbox: Mailbox, message_id: str) -> None:
        """Mark a single message as read."""
        mailbox.mark_read(message_id)

    def delete_mailbox(self, mailbox: Mailbox) -> None:
        """Delete (or clear) the mailbox."""
        mailbox.delete_mailbox()

    def download(self, mailbox: Mailbox, message: Message, attachment: Attachment | int) -> bytes:
        """Download an attachment from ``message``."""
        return mailbox.download(message, attachment)

    def wait_for_message(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> Message:
        """See :meth:`multitempmail.models.Mailbox.wait_for_message`."""
        return mailbox.wait_for_message(*args, **kwargs)

    def wait_for_subject(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> Message:
        """See :meth:`multitempmail.models.Mailbox.wait_for_subject`."""
        return mailbox.wait_for_subject(*args, **kwargs)

    def wait_for_sender(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> Message:
        """See :meth:`multitempmail.models.Mailbox.wait_for_sender`."""
        return mailbox.wait_for_sender(*args, **kwargs)

    def wait_for_otp(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> str:
        """See :meth:`multitempmail.models.Mailbox.wait_for_otp`."""
        return mailbox.wait_for_otp(*args, **kwargs)

    def wait_for_link(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> str:
        """See :meth:`multitempmail.models.Mailbox.wait_for_link`."""
        return mailbox.wait_for_link(*args, **kwargs)

    def wait_until(self, mailbox: Mailbox, *args: Any, **kwargs: Any) -> Any:
        """See :meth:`multitempmail.models.Mailbox.wait_until`."""
        return mailbox.wait_until(*args, **kwargs)
