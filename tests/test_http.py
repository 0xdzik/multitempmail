"""Unit tests for the HTTP layer: error mapping, retries, timeouts, UA."""

from __future__ import annotations

import httpx
import pytest

from multitempmail import errors
from multitempmail.http import DEFAULT_USER_AGENT, HTTPClient
from tests.conftest import Router, make_http


def test_success_passthrough():
    router = Router()
    router.on("GET", "/ping")(lambda r: httpx.Response(200, json={"ok": True}, request=r))
    http = make_http("https://api.example.test", router)
    assert http.request("GET", "/ping").json() == {"ok": True}


def test_default_user_agent(router):
    captured = {}

    def handler(request):
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={}, request=request)

    router.on("GET", "/ua")(handler)
    make_http("https://api.example.test", router).request("GET", "/ua")
    assert captured["ua"] == DEFAULT_USER_AGENT


def test_custom_user_agent(router):
    captured = {}

    def handler(request):
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={}, request=request)

    router.on("GET", "/ua")(handler)
    http = HTTPClient(
        "https://api.example.test",
        user_agent="my-agent/1.0",
        transport=httpx.MockTransport(router.handler),
    )
    http.request("GET", "/ua")
    assert captured["ua"] == "my-agent/1.0"


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (400, errors.ProviderError),
        (401, errors.AuthenticationError),
        (403, errors.AuthenticationError),
        (404, errors.MessageNotFound),
        (408, errors.TimeoutError),
        (410, errors.MailboxExpired),
        (429, errors.RateLimitError),
        (500, errors.ProviderError),
        (418, errors.ProviderError),
    ],
)
def test_status_mapping(status, exc_type):
    router = Router()
    router.on("GET", "/x")(lambda r: httpx.Response(status, request=r))
    http = make_http("https://api.example.test", router)
    with pytest.raises(exc_type) as excinfo:
        http.request("GET", "/x")
    assert excinfo.value.status_code == status


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"error": {"code": "x", "message": "y"}}, "HTTP 400: x: y"),
        ({"error": "code"}, "HTTP 400: error code"),
        ({"message": "m"}, "HTTP 400: m"),
        (None, "HTTP 400"),
    ],
)
def test_error_message_extraction(payload, expected):
    router = Router()
    router.on("GET", "/x")(
        lambda r: (
            httpx.Response(400, json=payload, request=r)
            if payload is not None
            else httpx.Response(400, request=r)
        )
    )
    http = make_http("https://api.example.test", router)
    with pytest.raises(errors.ProviderError) as excinfo:
        http.request("GET", "/x")
    assert str(excinfo.value) == expected


def test_retry_then_success():
    router = Router()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate_limit"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=2)
    assert http.request("GET", "/x").json() == {"ok": True}
    assert calls["n"] == 3


def test_retry_exhausted():
    router = Router()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, request=request)

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=2)
    with pytest.raises(errors.RateLimitError):
        http.request("GET", "/x")
    assert calls["n"] == 3


def test_no_retry_for_post():
    router = Router()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, request=request)

    router.on("POST", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=3)
    with pytest.raises(errors.RateLimitError):
        http.request("POST", "/x", json={})
    assert calls["n"] == 1


def test_retry_after_header():
    router = Router()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=request)
        return httpx.Response(200, request=request)

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=2)
    http.request("GET", "/x")
    assert calls["n"] == 2


def test_timeout_maps_to_timeout_error():
    router = Router()

    def handler(request):
        raise httpx.ReadTimeout("read timed out")

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=0)
    with pytest.raises(errors.TimeoutError):
        http.request("GET", "/x")


def test_timeout_retries():
    router = Router()
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router, retries=2)
    with pytest.raises(errors.TimeoutError):
        http.request("GET", "/x")
    assert calls["n"] == 3


def test_connect_error_maps_to_provider_error():
    router = Router()

    def handler(request):
        raise httpx.ConnectError("connection refused")

    router.on("GET", "/x")(handler)
    http = make_http("https://api.example.test", router)
    with pytest.raises(errors.ProviderError):
        http.request("GET", "/x")


def test_proxy_accepted():
    http = HTTPClient("https://api.example.test", proxy="http://localhost:9999")
    assert http.base_url == "https://api.example.test"
    http.close()


def test_base_headers_merged(router):
    captured = {}

    def handler(request):
        captured["h"] = request.headers.get("x-extra")
        return httpx.Response(200, json={}, request=request)

    router.on("GET", "/x")(handler)
    http = HTTPClient(
        "https://api.example.test",
        headers={"x-extra": "1"},
        transport=httpx.MockTransport(router.handler),
    )
    http.request("GET", "/x")
    assert captured["h"] == "1"
