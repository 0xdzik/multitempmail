"""Provider plugins. Importing this package registers every provider with
the registry (see :func:`multitempmail.get_provider`).

Only key-free providers live here. Providers that require a paid API key or
token (mail.cx, mail.td) are not shipped — they cannot be used without
credentials, so they only added a permanent failover branch that always
faulted and consumed a retry slot before every real attempt.
"""

from __future__ import annotations

from . import catchmail, mailtm, tempmail_lol

__all__ = ["catchmail", "mailtm", "tempmail_lol"]
