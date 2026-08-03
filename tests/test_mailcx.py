"""Tests for the mail.cx provider (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from tempmail import get_provider
from tests.conftest import Router, json_response, make_http

CONFIG = {
    "system_domains": [
        {"domain": "ddker.com", "default": False},
        {"domain": "9k3r.com", "default": True},
    ],
    "local_part_rules": {"max_length": 20, "min_length": 2, "pattern": "^[a-z0-9._-]+$"},
}


@pytest.fixture
def provider(router: Router):
    return get_provider("mailcx", http=make_http("https://api.mail.cx/v1", router))


def test_create_prefers_default_domain(router: Router, provider):
    @router.on("GET", "/v1/config")
    def config(request):
        return json_response(200, CONFIG, request)

    mb = provider.create()
    assert mb.address.endswith("@9k3r.com")
    local = mb.address.split("@")[0]
    assert 2 <= len(local) <= 20


def test_create_explicit_domain(router: Router, provider):
    @router.on("GET", "/v1/config")
    def config(request):
        return json_response(200, CONFIG, request)

    mb = provider.create(domain="custom.example", local_part="ab")
    assert mb.address == "ab@custom.example"


def test_messages_with_emails(router: Router, provider):
    @router.on("GET", "/v1/inbox/a@9k3r.com")
    def inbox(request):
        assert request.url.params.get("count") == "1"
        return json_response(
            200,
            {
                "emails": [
                    {
                        "id": "e1",
                        "from_email": "x@y.z",
                        "subject": "hi",
                        "created_at": "2024-01-02T03:04:05Z",
                    }
                ],
                "next_since": "cursor-1",
            },
            request,
        )

    mb = provider.attach("a@9k3r.com")
    msgs = provider.messages(mb)
    assert msgs[0].id == "e1"
    assert msgs[0].sender == "x@y.z"
    assert provider._cursors["a@9k3r.com"] == "cursor-1"


def test_messages_204_empty(router: Router, provider):
    @router.on("GET", "/v1/inbox/a@9k3r.com")
    def inbox(request):
        return httpx.Response(204, request=request)

    assert provider.messages(provider.attach("a@9k3r.com")) == []


def test_wait_uses_cursor(router: Router, provider):
    calls = []

    @router.on("GET", "/v1/inbox/a@9k3r.com")
    def inbox(request):
        calls.append(request.url.params.get("since"))
        if len(calls) == 1:
            return json_response(200, {"emails": [], "next_since": "cur-9"}, request)
        assert request.url.params.get("since") == "cur-9"
        return json_response(
            200,
            {"emails": [{"id": "e9", "from_email": "n@m.o"}], "next_since": "cur-10"},
            request,
        )

    mb = provider.attach("a@9k3r.com")
    assert provider.wait(mb, timeout=10) == []
    msgs = provider.wait(mb, timeout=10)
    assert msgs[0].id == "e9"
    assert calls == [None, "cur-9"]


def test_message_defensive_parse(router: Router, provider):
    payload = {
        "id": "e1",
        "from_email": "x@y.z",
        "subject": "s",
        "text": "plain",
        "attachments": [{"filename": "a.txt", "content_type": "text/plain", "size": 3}],
    }

    @router.on("GET", "/v1/email/e1")
    def email(request):
        return json_response(200, payload, request)

    msg = provider.message(provider.attach("a@9k3r.com"), "e1")
    assert msg.body_text == "plain"
    assert msg.attachments[0].filename == "a.txt"
    assert msg.attachments[0].index == 0


def test_delete_message_and_clear_mailbox(router: Router, provider):
    seen = []

    @router.on("DELETE", "/v1/email/e1")
    def delete_email(request):
        seen.append("email")
        return httpx.Response(204, request=request)

    @router.on("DELETE", "/v1/inbox/a@9k3r.com")
    def clear(request):
        seen.append("inbox")
        return httpx.Response(204, request=request)

    mb = provider.attach("a@9k3r.com")
    provider.delete_message(mb, "e1")
    provider.delete_mailbox(mb)
    assert seen == ["email", "inbox"]


def test_download_attachment_by_index(router: Router, provider):
    from tempmail.models import Attachment

    @router.on("GET", "/v1/email/e1/attachments/1")
    def attachment(request):
        return httpx.Response(200, content=b"bytes", request=request)

    mb = provider.attach("a@9k3r.com")
    att = Attachment(filename="a.txt", index=1)
    assert provider.download_attachment(mb, "e1", att) == b"bytes"


def test_api_token_header(router: Router):
    provider = get_provider(
        "mailcx", api_token="tm_live_x", http=make_http("https://api.mail.cx/v1", router)
    )

    @router.on("GET", "/v1/inbox/a@9k3r.com")
    def inbox(request):
        assert request.headers.get("x-api-token") == "tm_live_x"
        return json_response(200, {"emails": [], "next_since": "c"}, request)

    provider.messages(provider.attach("a@9k3r.com"))
