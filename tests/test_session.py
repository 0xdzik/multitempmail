"""Tests for session persistence (save/load roundtrip)."""

from __future__ import annotations

import json

import pytest
from conftest import Router, json_response, make_http

from multitempmail.base import get_provider
from multitempmail.client import TempMailClient
from multitempmail.session import SESSION_VERSION, load_session


def test_save_load_catchmail_mailbox(tmp_path) -> None:
    path = tmp_path / "session.json"
    client = TempMailClient("catchmail")
    mailbox = client.create()
    client.save_session(str(path))
    payload = json.loads(path.read_text())
    assert payload["version"] == SESSION_VERSION
    assert payload["providers"] == ["catchmail"]
    assert payload["mailbox"]["address"] == mailbox.address

    restored = TempMailClient.load_session(str(path))
    assert restored.name == "catchmail"
    assert restored.active is not None
    assert restored.active.address == mailbox.address
    assert restored.active.provider == "catchmail"


def test_load_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        TempMailClient.load_session(str(tmp_path / "nope.json"))


def test_load_bad_version_raises(tmp_path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"version": 99, "providers": ["mailtm"]}))
    with pytest.raises(ValueError):
        TempMailClient.load_session(str(path))


def test_load_empty_providers_raises(tmp_path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"version": SESSION_VERSION, "providers": []}))
    with pytest.raises(ValueError):
        TempMailClient.load_session(str(path))


def test_roundtrip_restores_token_and_options(tmp_path) -> None:
    router = Router()

    @router.on("GET", "/v3/inboxes/tok/emails")
    def emails(request):
        return json_response(200, [])

    path = tmp_path / "session.json"
    provider = get_provider("tempmail_lol", http=make_http("https://api.tempmail.lol", router))
    client = TempMailClient(provider)
    mailbox = provider.attach("user@tempmail.lol", token="tok")
    client._active = mailbox
    client.save_session(str(path))

    restored = TempMailClient.load_session(str(path))
    assert restored.active is not None
    assert restored.active.address == "user@tempmail.lol"
    assert restored.active.token == "tok"
    assert restored.active._provider is not None


def test_restore_session_in_place(tmp_path) -> None:
    path = tmp_path / "session.json"
    first = TempMailClient("catchmail")
    first.create()
    first.save_session(str(path))

    second = TempMailClient("mailtm")
    second.restore_session(str(path))
    assert second.name == "catchmail"


def test_missing_mailbox_provider_raises(tmp_path) -> None:
    from multitempmail.session import save_session

    path = tmp_path / "session.json"
    client = TempMailClient("catchmail")
    mailbox = client.create()
    save_session(str(path), providers=["mailtm"], options={}, mailbox=mailbox)
    with pytest.raises(ValueError):
        TempMailClient.load_session(str(path))


def test_module_level_save_load(tmp_path) -> None:
    from multitempmail.session import save_session

    path = tmp_path / "s.json"
    save_session(str(path), providers=["catchmail"], options={"timeout": 12.5})
    payload = load_session(str(path))
    assert payload["options"] == {"timeout": 12.5}
