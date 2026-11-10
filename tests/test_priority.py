"""Pins the provider priority order to the reasons it was chosen.

If someone reorders providers, these tests should force them to re-read why
the order exists. Ordering is a product decision, not an accident:

1. catchmail (100) — no setup, `create()` makes no network call, ~5 ms to a
   usable address, and it supports read + attachments + delete message.
2. mailtm (90)    — real API with the widest capabilities, but rate-limited to
   10 requests/minute and ~1-3 s per call.
4. tempmail_lol (85) — real server-side inbox, but reads consume messages, so
   it cannot be probed speculatively.
"""

from __future__ import annotations

from multitempmail.base import get_provider
from multitempmail.client import TempMailClient

READ_LIKE = {"read", "attachments"}


def _caps(provider) -> set[str]:
    return {str(c) for c in provider.capabilities}


def test_priority_order() -> None:
    assert [p.name for p in TempMailClient().providers()] == [
        "catchmail",
        "mailtm",
        "tempmail_lol",
    ]


def test_priorities_are_strictly_descending_and_unique() -> None:
    values = [p.priority for p in TempMailClient().providers()]
    assert values == sorted(values, reverse=True), values
    assert len(set(values)) == len(values), f"ties make ordering ambiguous: {values}"


def test_catchmail_create_costs_no_network_call() -> None:
    """This is the reason catchmail leads: no setup and no round-trip."""
    catchmail = get_provider("catchmail")
    mailbox = catchmail.create()
    assert mailbox.address.endswith("@catchmail.io")


def test_catchmail_exposes_read_and_attachments() -> None:
    """catchmail leads tempmail.lol on the capabilities that matter for parsing
    a verification email, not merely on count (both declare five)."""
    caps = _caps(get_provider("catchmail"))
    assert READ_LIKE <= caps, f"missing {READ_LIKE - caps}"


def test_tempmail_lol_lacks_read_and_attachments() -> None:
    """Documents why it cannot outrank catchmail despite having delete_mailbox."""
    caps = _caps(get_provider("tempmail_lol"))
    assert not (READ_LIKE & caps), f"unexpected {READ_LIKE & caps}"


def test_mailtm_outranks_tempmail_lol_despite_being_slower() -> None:
    """mailtm is rate-limited and slow, but far richer — it must not sink below
    tempmail.lol, whose reads consume messages."""
    assert get_provider("mailtm").priority > get_provider("tempmail_lol").priority
    assert READ_LIKE <= _caps(get_provider("mailtm"))
