"""Tests for the tempmail.lol provider (v3 API, mocked HTTP)."""

from __future__ import annotations

import time

import httpx
import pytest

from tempmail import get_provider
from tempmail.errors import AuthenticationError, UnsupportedFeature
from tests.conftest import Router, json_response, make_http, request_body


@pytest.fixture
def provider(router: Router):
    return get_provider("tempmail_lol", http=make_http("https://api.tempmail.lol", router))


def test_create(router: Router, provider):
    @router.on("POST", "/v3/inboxes")
    def create(request):
        assert request_body(request) == {}
        now_ms = int(time.time() * 1000)
        return json_response(
            201,
            {
                "address": "u@tmail.wtf",
                "token": "tok-1",
                "created_at": now_ms,
                "expires_at": now_ms + 3_600_000,
                "expired": False,
            },
            request,
        )

    mb = provider.create()
    assert mb.address == "u@tmail.wtf"
    assert mb.token == "tok-1"
    assert mb.expires_at is not None
    assert not mb.expired


def test_create_with_options(router: Router, provider):
    @router.on("POST", "/v3/inboxes")
    def create(request):
        assert request_body(request) == {"prefix": "pre", "domain": "d.com", "community": True}
        return json_response(201, {"address": "pre@d.com", "token": "t"}, request)

    mb = provider.create(prefix="pre", domain="d.com", community=True)
    assert mb.address == "pre@d.com"


def test_attach_requires_token(provider):
    with pytest.raises(AuthenticationError):
        provider.attach("u@tmail.wtf")


def test_messages(router: Router, provider):
    @router.on("GET", "/v3/inboxes/tok-1/emails")
    def emails(request):
        return json_response(
            200,
            {"emails": [{"id": "e1", "from": "x@y.z", "subject": "hi", "body": "code 123456"}]},
            request,
        )

    mb = provider.attach("u@tmail.wtf", token="tok-1")
    msgs = provider.messages(mb)
    assert msgs[0].id == "e1"
    assert msgs[0].body_text == "code 123456"


def test_wait_long_poll(router: Router, provider):
    @router.on("GET", "/v3/inboxes/tok-1/wait")
    def wait(request):
        assert request.url.params.get("timeout") == "10"
        return json_response(
            200,
            {
                "emails": [{"id": "e2", "from": "a@b.c", "subject": "s", "body": "b"}],
                "timed_out": False,
                "waited_ms": 100,
            },
            request,
        )

    mb = provider.attach("u@tmail.wtf", token="tok-1")
    msgs = provider.wait(mb, timeout=10)
    assert msgs[0].id == "e2"


def test_wait_timeout_capped_at_60(router: Router, provider):
    @router.on("GET", "/v3/inboxes/tok-1/wait")
    def wait(request):
        assert request.url.params.get("timeout") == "60"
        return json_response(200, {"emails": [], "timed_out": True, "waited_ms": 60000}, request)

    mb = provider.attach("u@tmail.wtf", token="tok-1")
    assert provider.wait(mb, timeout=120) == []


def test_delete_endpoints(router: Router, provider):
    seen = []

    @router.on("DELETE", "/v3/inboxes/tok-1/emails/e1")
    def delete_message(request):
        seen.append("message")
        return httpx.Response(204, request=request)

    @router.on("DELETE", "/v3/inboxes/tok-1")
    def delete_inbox(request):
        seen.append("inbox")
        return httpx.Response(204, request=request)

    mb = provider.attach("u@tmail.wtf", token="tok-1")
    provider.delete_message(mb, "e1")
    provider.delete_mailbox(mb)
    assert seen == ["message", "inbox"]


def test_api_key_header_sent(router: Router):
    provider = get_provider(
        "tempmail_lol", api_key="key-1", http=make_http("https://api.tempmail.lol", router)
    )

    @router.on("POST", "/v3/inboxes")
    def create(request):
        assert request.headers.get("authorization") == "Bearer key-1"
        return json_response(201, {"address": "u@tmail.wtf", "token": "t"}, request)

    provider.create()


def test_unsupported_operations(provider):
    mb = provider.attach("u@tmail.wtf", token="tok-1")
    with pytest.raises(UnsupportedFeature):
        provider.message(mb, "e1")
    with pytest.raises(UnsupportedFeature):
        provider.mark_read(mb, "e1")
    with pytest.raises(UnsupportedFeature):
        provider.download_attachment(mb, "e1", None)
