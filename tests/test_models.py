"""Tests for the data models (Mailbox/Message/Attachment)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tempmail.errors import TempMailError
from tempmail.models import Address, Attachment, Mailbox, Message


def make_mailbox(**kw):
    return Mailbox(provider="test", address="a@b.c", **kw)


def test_mailbox_expired_property():
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    assert make_mailbox(expires_at=past).expired is True
    assert make_mailbox(expires_at=future).expired is False
    assert make_mailbox().expired is False


def test_unbound_mailbox_raises():
    with pytest.raises(TempMailError, match="not attached"):
        make_mailbox().messages()


def test_message_dataclass():
    msg = Message(
        id="1",
        sender="sender@x.com",
        sender_name="Sender",
        to=(Address(address="a@b.c", name="A"),),
        subject="Hi",
        body_text="body",
        attachments=(Attachment(filename="f.txt", index=0),),
    )
    assert msg.to[0].address == "a@b.c"
    assert msg.attachments[0].filename == "f.txt"


class FakeProvider:
    def __init__(self, capabilities, payloads=None):
        self.capabilities = capabilities
        self.payloads = payloads or {}
        self.calls = []

    def supports(self, cap):
        return cap in self.capabilities

    def download_attachment(self, mailbox, message_id, attachment):
        self.calls.append((message_id, attachment.index))
        return b"data"


def test_mailbox_download_by_index():
    provider = FakeProvider({"attachments"})
    msg = Message(id="m1", attachments=(Attachment(filename="a.txt", index=0),))
    mailbox = Mailbox(provider="test", address="a@b.c", _provider=provider)
    assert mailbox.download(msg, 0) == b"data"
    assert provider.calls == [("m1", 0)]
    assert mailbox.download(msg, msg.attachments[0]) == b"data"


def test_mailbox_download_index_out_of_range():
    provider = FakeProvider({"attachments"})
    msg = Message(id="m1", attachments=(Attachment(filename="a.txt", index=0),))
    mailbox = Mailbox(provider="test", address="a@b.c", _provider=provider)
    with pytest.raises(TempMailError, match="out of range"):
        mailbox.download(msg, 3)


def test_attach_exposes_provider_name():
    provider = FakeProvider(set())
    mailbox = Mailbox(provider="test", address="a@b.c", _provider=provider)
    assert mailbox.provider == "test"
