"""multitempmail — a unified SDK for temporary email providers.

Supported providers: mail.tm, catchmail.io, tempmail.lol.

Quickstart::

    from multitempmail import TempMailClient

    client = TempMailClient()          # every provider, automatic failover
    mailbox = client.create()
    otp = mailbox.wait_for_otp(timeout=120)
"""

from __future__ import annotations

__version__ = "0.2.0"

# Importing providers registers them with the registry.
from . import providers as _providers  # noqa: F401
from .base import (
    BaseProvider,
    ProviderInfo,
    get_aliases,
    get_provider,
    list_providers,
    register,
)
from .cache import ResponseCache
from .client import TempMailClient
from .errors import (
    AuthenticationError,
    MailboxExpired,
    MessageNotFound,
    ProviderError,
    RateLimitError,
    TempMailError,
    TimeoutError,
    UnsupportedFeature,
    WaitCancelled,
)
from .failover import FailoverError, ProviderPool
from .health import HealthManager, HealthSnapshot
from .models import Address, Attachment, Mailbox, Message
from .session import load_session, save_session
from .utils import extract_link, extract_links, extract_otp, extract_otps

__all__ = [
    "Address",
    "Attachment",
    "AuthenticationError",
    "BaseProvider",
    "FailoverError",
    "HealthManager",
    "HealthSnapshot",
    "Mailbox",
    "MailboxExpired",
    "Message",
    "MessageNotFound",
    "ProviderError",
    "ProviderInfo",
    "ProviderPool",
    "RateLimitError",
    "ResponseCache",
    "TempMailClient",
    "TempMailError",
    "TimeoutError",
    "UnsupportedFeature",
    "WaitCancelled",
    "__version__",
    "extract_link",
    "extract_links",
    "extract_otp",
    "extract_otps",
    "get_aliases",
    "get_provider",
    "list_providers",
    "load_session",
    "register",
    "save_session",
]
