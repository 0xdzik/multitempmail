"""Tests for the provider registry and the TempMailClient facade."""

from __future__ import annotations

import httpx
import pytest

from multitempmail import TempMailClient, get_provider, list_providers
from multitempmail.caps import Cap
from multitempmail.errors import TempMailError
from tests.conftest import Router, json_response, make_http


def test_list_providers():
    assert list_providers() == ["catchmail", "mailtm", "tempmail_lol"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mailtm", "mailtm"),
        ("MAILTM", "mailtm"),
        ("mail.tm", "mailtm"),
        ("catchmail", "catchmail"),
        ("catchmail.io", "catchmail"),
        ("tempmail_lol", "tempmail_lol"),
        ("tempmail.lol", "tempmail_lol"),
        ("tempmail", "tempmail_lol"),
    ],
)
def test_provider_aliases(name, expected):
    assert get_provider(name).name == expected


@pytest.mark.parametrize("removed", ["mailcx", "mail.cx", "mailtd", "mail.td"])
def test_keyed_providers_are_gone(removed):
    """Providers that required a paid API key/token are no longer shipped."""
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider(removed)


def test_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("nope")


def test_default_provider_is_mailtm():
    assert TempMailClient().name == "mailtm"


def test_client_accepts_provider_instance():
    provider = get_provider("catchmail")
    assert TempMailClient(provider).name == "catchmail"


def test_client_unknown_options_rejected():
    with pytest.raises(TypeError, match="unexpected options"):
        get_provider("catchmail", bogus=1)


def test_client_create_and_attach_delegate():
    router = Router()
    client = TempMailClient("catchmail", http=make_http("https://api.catchmail.io", router))
    mailbox = client.create(local_part="mytests")
    assert mailbox.address == "mytests@catchmail.io"
    assert client.name == "catchmail"
    attached = client.mailbox("other@catchmail.io")
    assert attached.address == "other@catchmail.io"
    assert attached.provider == "catchmail"


def test_client_message_delegations(router):
    router.on("GET", "/api/v1/mailbox")(
        lambda r: json_response(
            200,
            {
                "address": "a@catchmail.io",
                "count": 1,
                "messages": [{"id": "m1", "from": "x@y.z", "subject": "hi"}],
            },
            r,
        )
    )
    router.on("DELETE", "/api/v1/message/m1")(lambda r: httpx.Response(204, request=r))
    client = TempMailClient("catchmail", http=make_http("https://api.catchmail.io", router))
    mailbox = client.mailbox("a@catchmail.io")
    messages = client.messages(mailbox)
    assert messages[0].id == "m1"
    client.delete(mailbox, "m1")


def test_client_supports():
    client = TempMailClient("catchmail")
    assert client.supports(Cap.LIST)
    assert not client.supports(Cap.DELETE_MAILBOX)


def test_unsupported_operation_raises():
    client = TempMailClient("catchmail")
    with pytest.raises(TempMailError):
        client.provider.delete_mailbox(client.mailbox("a@catchmail.io"))
