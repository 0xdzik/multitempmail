"""Tests for the multitempmail CLI (argparse, mocked HTTP, temp sessions)."""

from __future__ import annotations

import json

from conftest import Router, json_response, make_http

from multitempmail import cli
from multitempmail.client import TempMailClient


def _mock_client(monkeypatch, provider_name: str, router: Router) -> TempMailClient:
    bases = {
        "mailtm": "https://api.mail.tm",
        "mailcx": "https://api.mail.cx/v1",
        "catchmail": "https://api.catchmail.io",
    }
    http = make_http(bases[provider_name], router)
    client = TempMailClient(provider_name, http=http)
    monkeypatch.setattr(cli, "TempMailClient", lambda *a, **k: client)
    monkeypatch.setattr(cli, "_client_for", lambda session, provider=None: client)
    return client


def test_providers_lists_all(capsys) -> None:
    assert cli.main(["providers"]) == 0
    out = capsys.readouterr().out
    for name in ("mailtm", "mailcx", "catchmail", "tempmail_lol", "mailtd"):
        assert name in out


def test_providers_json(capsys) -> None:
    assert cli.main(["providers", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {p["name"] for p in payload} == {
        "mailtm",
        "mailcx",
        "catchmail",
        "tempmail_lol",
        "mailtd",
    }
    mailtm = next(p for p in payload if p["name"] == "mailtm")
    assert mailtm["priority"] == 100
    assert "catchmail.io" in next(p for p in payload if p["name"] == "catchmail")["known_domains"]


def test_create_prints_address(monkeypatch, capsys, tmp_path) -> None:
    router = Router()
    _mock_client(monkeypatch, "catchmail", router)
    assert cli.main(["create", "--provider", "catchmail", "--save", str(tmp_path / "s.json")]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("@catchmail.io")
    assert (tmp_path / "s.json").is_file()


def test_inbox_prints_messages(monkeypatch, capsys, tmp_path) -> None:
    router = Router()

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        return json_response(
            200,
            {"messages": [{"id": "m1", "from": "s@x.io", "subject": "Welcome"}]},
        )

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["inbox", "--session", str(tmp_path / "s.json")]) == 0
    out = capsys.readouterr().out
    assert "m1" in out
    assert "Welcome" in out


def test_read_prints_body(monkeypatch, capsys, tmp_path) -> None:
    router = Router()

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        return json_response(
            200,
            {
                "id": "m1",
                "from": "s@x.io",
                "subject": "Hi",
                "body": {"text": "hello world"},
            },
        )

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["read", "m1", "--session", str(tmp_path / "s.json")]) == 0
    out = capsys.readouterr().out
    assert "hello world" in out
    assert "Hi" in out


def test_otp_waits_and_prints(monkeypatch, capsys, tmp_path) -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response(200, {"messages": []})
        return json_response(
            200,
            {"messages": [{"id": "m1", "from": "s@x.io", "subject": "code 123456"}]},
        )

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        return json_response(
            200,
            {
                "id": "m1",
                "from": "s@x.io",
                "subject": "code 123456",
                "body": {"text": "Your code is 123456"},
            },
        )

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["otp", "--timeout", "2", "--session", str(tmp_path / "s.json")]) == 0
    assert capsys.readouterr().out.strip() == "123456"


def test_wait_prints_message(monkeypatch, capsys, tmp_path) -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response(200, {"messages": []})
        return json_response(
            200,
            {"messages": [{"id": "m2", "from": "s@x.io", "subject": "Invoice"}]},
        )

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["wait", "--subject", "Invoice", "--timeout", "2"]) == 0
    out = capsys.readouterr().out
    assert "m2" in out


def test_delete_message(monkeypatch, capsys, tmp_path) -> None:
    router = Router()

    @router.on("DELETE", "/api/v1/message/m1")
    def delete(request):
        return json_response(204, None)

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["delete", "m1", "--session", str(tmp_path / "s.json")]) == 0
    assert "deleted message m1" in capsys.readouterr().out


def test_health_reports(capsys) -> None:
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert "mailtm" in out
    assert "healthy" in out


def test_domains_from_known_domains(capsys) -> None:
    assert cli.main(["domains", "--provider", "catchmail"]) == 0
    assert capsys.readouterr().out.strip() == "catchmail.io"


def test_domains_fetched_via_api(monkeypatch, capsys) -> None:
    router = Router()

    @router.on("GET", "/domains")
    def domains(request):
        return json_response(
            200,
            {"hydra:member": [{"domain": "mail.tm", "isActive": True, "isPrivate": False}]},
        )

    _mock_client(monkeypatch, "mailtm", router)
    assert cli.main(["domains", "--provider", "mailtm"]) == 0
    assert capsys.readouterr().out.strip() == "mail.tm"


def test_error_exit_code_and_stderr(monkeypatch, capsys, tmp_path) -> None:
    router = Router()

    @router.on("GET", "/api/v1/message/zzz")
    def message(request):
        return json_response(404, {"error": {"code": "not_found", "message": "nope"}})

    client = _mock_client(monkeypatch, "catchmail", router)
    mailbox = client.create()
    client._active = mailbox
    assert cli.main(["read", "zzz"]) == 1
    captured = capsys.readouterr()
    assert "error" in captured.err
