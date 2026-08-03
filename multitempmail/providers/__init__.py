"""Provider plugins. Importing this package registers every provider with
the registry (see :func:`multitempmail.get_provider`)."""

from __future__ import annotations

from . import catchmail, mailcx, mailtd, mailtm, tempmail_lol

__all__ = ["catchmail", "mailcx", "mailtd", "mailtm", "tempmail_lol"]
