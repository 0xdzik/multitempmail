"""Tests for the response cache and its mailbox integration."""

from __future__ import annotations

import time

from conftest import Router, json_response, make_http

from multitempmail.base import get_provider
from multitempmail.cache import ResponseCache


def test_get_miss_then_set() -> None:
    cache = ResponseCache(default_ttl=30.0)
    assert cache.get("k") is None
    cache.set("k", {"a": 1})
    assert cache.get("k") == {"a": 1}
    assert "k" in cache
    assert cache.size == 1
    assert cache.keys == ["k"]


def test_ttl_expiry() -> None:
    cache = ResponseCache(default_ttl=30.0)
    cache.set("k", "v", ttl=0.01)
    assert cache.get("k") == "v"
    time.sleep(0.02)
    assert cache.get("k") is None
    assert cache.size == 0


def test_invalidate_and_clear() -> None:
    cache = ResponseCache()
    cache.set("a:1", "x")
    cache.set("a:2", "y")
    cache.set("b:1", "z")
    assert cache.invalidate("a:1") is True
    assert cache.invalidate("a:1") is False
    assert cache.invalidate_prefix("a:") == 1
    assert cache.invalidate_prefix("a:") == 0
    cache.clear()
    assert cache.size == 0


def test_key_types_normalized() -> None:
    cache = ResponseCache()
    cache.set(("messages", "mailtm", "a@b.c"), "v")
    assert cache.get(("messages", "mailtm", "a@b.c")) == "v"


def test_mailbox_messages_cached() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        calls["n"] += 1
        return json_response(200, {"messages": [{"id": 1, "from": "x@y.z", "subject": "hi"}]})

    provider = get_provider(
        "catchmail",
        http=make_http("https://api.catchmail.io", router),
        cache=ResponseCache(),
    )
    mailbox = provider.create()
    first = mailbox.messages()
    second = mailbox.messages()
    assert [m.id for m in first] == ["1"]
    assert [m.id for m in second] == ["1"]
    assert calls["n"] == 1


def test_delete_invalidates_messages_cache() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        calls["n"] += 1
        return json_response(200, {"messages": [{"id": 1, "from": "x@y.z", "subject": "hi"}]})

    @router.on("DELETE", "/api/v1/message/1")
    def delete(request):
        return json_response(204, None)

    provider = get_provider(
        "catchmail",
        http=make_http("https://api.catchmail.io", router),
        cache=ResponseCache(),
    )
    mailbox = provider.create()
    mailbox.messages()
    mailbox.delete("1")
    mailbox.messages()
    assert calls["n"] == 2


def test_mark_read_invalidates_messages_cache() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/messages")
    def messages(request):
        calls["n"] += 1
        return json_response(
            200,
            {"hydra:member": [{"id": 1, "from": {"address": "x@y.z"}, "subject": "hi"}]},
        )

    @router.on("PATCH", "/messages/1")
    def patch(request):
        return json_response(204, None)

    provider = get_provider(
        "mailtm",
        http=make_http("https://api.mail.tm", router),
        cache=ResponseCache(),
    )
    mailbox = provider.attach("a@b.c", token="tok")
    mailbox.messages()
    mailbox.mark_read("1")
    mailbox.messages()
    assert calls["n"] == 2


def test_cache_disabled_fetches_every_time() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox_handler(request):
        calls["n"] += 1
        return json_response(200, {"messages": []})

    provider = get_provider(
        "catchmail", http=make_http("https://api.catchmail.io", router), cache=None
    )
    mailbox = provider.create()
    mailbox.messages()
    mailbox.messages()
    assert calls["n"] == 2


def test_consuming_provider_never_caches() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/v3/inboxes/tok/emails")
    def emails(request):
        calls["n"] += 1
        return json_response(
            200, {"emails": [{"id": 1, "from": "x@y.z", "subject": "hi", "body": "b"}]}
        )

    provider = get_provider(
        "tempmail_lol",
        http=make_http("https://api.tempmail.lol", router),
        cache=ResponseCache(),
    )
    mailbox = provider.attach("a@b.c", token="tok")
    mailbox.messages()
    mailbox.messages()
    assert calls["n"] == 2


def test_domains_cached_via_provider_cache() -> None:
    router = Router()
    calls = {"n": 0}

    @router.on("GET", "/domains")
    def domains(request):
        calls["n"] += 1
        return json_response(200, {"hydra:member": [{"id": "u1", "domain": "x.tm", "isActive": True}]})

    provider = get_provider(
        "mailtm",
        http=make_http("https://api.mail.tm", router),
        cache=ResponseCache(),
    )
    assert provider.domains() == [{"id": "u1", "domain": "x.tm", "isActive": True}]
    assert provider.domains() == [{"id": "u1", "domain": "x.tm", "isActive": True}]
    assert calls["n"] == 1
    provider.clear_cache()
    assert provider.domains() == [{"id": "u1", "domain": "x.tm", "isActive": True}]
    assert calls["n"] == 2
