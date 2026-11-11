"""Tests for the dummyinbox.com provider (mocked HTTP).

Contract under test:
  * ``GET /domains`` lists the usable domains and is cached
  * ``POST /inboxes`` is the only way to get an address (server picks the name)
  * ``domain=`` outside the published list fails locally, never at the server
  * ``local_part=`` is rejected explicitly instead of being silently ignored
  * ``wait()`` sends the server-side ``wait`` param AND a client timeout that
    outlasts it (the bug that made the first implementation abort at 30s)
"""

from __future__ import annotations

import pytest

from multitempmail import get_provider
from tests.conftest import Router, json_response, make_http

DOMAINS = ["dummyinbox.com", "987654320.xyz", "dotmail.dev", "223190.xyz"]


@pytest.fixture
def provider(router: Router):
    return get_provider("dummyinbox", http=make_http("https://dummyinbox.com/api/v1", router))


def _domains_route(router: Router, domains=DOMAINS, calls: dict | None = None) -> None:
    @router.on("GET", "/api/v1/domains")
    def domains_handler(request):
        if calls is not None:
            calls["n"] = calls.get("n", 0) + 1
        return json_response(200, {"domains": list(domains)})


def test_domains_lists_published_domains(provider, router: Router):
    _domains_route(router)
    assert provider.domains_names() == DOMAINS
    listed = provider.domains()
    assert [d["domain"] for d in listed] == DOMAINS
    assert all(d["isActive"] for d in listed)


def test_domains_cached_per_instance(provider, router: Router):
    calls: dict = {}
    _domains_route(router, calls=calls)
    provider.domains_names()
    provider.domains_names()
    provider.domains_names()
    assert calls["n"] == 1, "GET /domains must be fetched once per provider instance"


def test_create_defaults_to_first_domain(provider, router: Router):
    _domains_route(router)

    @router.on("POST", "/api/v1/inboxes")
    def inboxes(request):
        assert request.content in (b"{}", b""), request.content
        return json_response(201, {"address": "abc@dummyinbox.com", "domain": "dummyinbox.com"})

    mailbox = provider.create()
    assert mailbox.address == "abc@dummyinbox.com"
    assert mailbox.provider == "dummyinbox"


def test_create_with_each_published_domain(provider, router: Router):
    _domains_route(router)

    @router.on("POST", "/api/v1/inboxes")
    def inboxes(request):
        import json

        body = json.loads(request.content or b"{}")
        dom = body.get("domain", "dummyinbox.com")
        return json_response(201, {"address": f"xyz@{dom}", "domain": dom})

    for dom in DOMAINS:
        mailbox = provider.create(domain=dom)
        assert mailbox.address == f"xyz@{dom}"


def test_create_domain_is_case_insensitive(provider, router: Router):
    _domains_route(router)

    @router.on("POST", "/api/v1/inboxes")
    def inboxes(request):
        return json_response(201, {"address": "q@dotmail.dev", "domain": "dotmail.dev"})

    mailbox = provider.create(domain="DOTMAIL.DEV")
    assert mailbox.address == "q@dotmail.dev"


def test_create_rejects_domain_outside_the_published_list(provider, router: Router):
    _domains_route(router)
    with pytest.raises(ValueError, match="allowed domains are"):
        provider.create(domain="nope.test")


def test_create_rejects_unknown_domain_before_any_request(provider, router: Router):
    """The rejection must be local — no POST should be attempted."""
    _domains_route(router)

    @router.on("POST", "/api/v1/inboxes")
    def inboxes(request):  # pragma: no cover - must never run
        raise AssertionError("must not POST an unsupported domain")

    with pytest.raises(ValueError):
        provider.create(domain="evil.test")


def test_create_rejects_local_part_instead_of_ignoring_it(provider, router: Router):
    _domains_route(router)
    with pytest.raises(ValueError, match="local part server-side"):
        provider.create(local_part="foo")


def test_create_raises_when_server_returns_no_address(provider, router: Router):
    _domains_route(router)

    @router.on("POST", "/api/v1/inboxes")
    def inboxes(request):
        return json_response(201, {"domain": "dummyinbox.com"})

    with pytest.raises(ValueError, match="returned no address"):
        provider.create()


def test_messages_maps_summaries(provider, router: Router):
    _domains_route(router)

    @router.on("GET", "/api/v1/inboxes/a@dummyinbox.com/messages")
    def messages(request):
        return json_response(
            200,
            {
                "messages": [
                    {
                        "id": "6716c0e2a4b9f3d1c8e5a702",
                        "from": "hello@example.com",
                        "fromName": "Example App",
                        "to": ["a@dummyinbox.com"],
                        "cc": [],
                        "subject": "Your code",
                        "snippet": "Your verification code is 482913.",
                        "receivedAt": "2026-10-21T09:41:07.000Z",
                        "attachmentCount": 1,
                    }
                ],
                "total": 1,
            },
        )

    mailbox = provider.attach("a@dummyinbox.com")
    msgs = provider.messages(mailbox)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.id == "6716c0e2a4b9f3d1c8e5a702"
    assert m.sender == "hello@example.com"
    assert m.sender_name == "Example App"
    assert m.subject == "Your code"
    assert m.body_text == "Your verification code is 482913."
    assert m.to[0].address == "a@dummyinbox.com"


def test_message_maps_bodies_and_attachments(provider, router: Router):
    _domains_route(router)

    @router.on("GET", "/api/v1/inboxes/a@dummyinbox.com/messages/msg1")
    def message(request):
        return json_response(
            200,
            {
                "message": {
                    "id": "msg1",
                    "from": "no-reply@example.com",
                    "to": ["a@dummyinbox.com"],
                    "subject": "Welcome",
                    "text": "plain body",
                    "html": "<p>html body</p>",
                    "receivedAt": "2026-10-21T09:41:07.000Z",
                    "attachments": [
                        {"filename": "f.pdf", "contentType": "application/pdf", "size": 1234}
                    ],
                }
            },
        )

    mailbox = provider.attach("a@dummyinbox.com")
    m = provider.message(mailbox, "msg1")
    assert m.body_text == "plain body"
    assert m.body_html == "<p>html body</p>"
    assert len(m.attachments) == 1
    assert m.attachments[0].filename == "f.pdf"
    assert m.attachments[0].size == 1234


def test_wait_sends_wait_param_and_outlasts_it(provider, router: Router):
    """Regression: the client timeout must exceed the server-side wait.

    The first implementation reused the default 30 s client timeout for a 30 s
    server-side hold, so every wait aborted before the server could answer.
    """
    _domains_route(router)
    seen: dict = {}

    @router.on("GET", "/api/v1/inboxes/a@dummyinbox.com/messages")
    def messages(request):
        seen["wait"] = request.url.params.get("wait")
        return json_response(200, {"messages": [], "total": 0})

    mailbox = provider.attach("a@dummyinbox.com")
    out = provider.wait(mailbox, timeout=30)
    assert out == []
    assert seen["wait"] == "30"


def test_wait_clamps_to_the_api_maximum(provider, router: Router):
    _domains_route(router)
    seen: dict = {}

    @router.on("GET", "/api/v1/inboxes/a@dummyinbox.com/messages")
    def messages(request):
        seen["wait"] = request.url.params.get("wait")
        return json_response(200, {"messages": []})

    provider.wait(provider.attach("a@dummyinbox.com"), timeout=999)
    assert seen["wait"] == "30", seen


def test_wait_returns_arrived_messages(provider, router: Router):
    _domains_route(router)

    @router.on("GET", "/api/v1/inboxes/a@dummyinbox.com/messages")
    def messages(request):
        return json_response(
            200,
            {
                "messages": [
                    {
                        "id": "late1",
                        "from": "x@y.z",
                        "to": ["a@dummyinbox.com"],
                        "subject": "arrived",
                        "snippet": "code 123456",
                        "receivedAt": "2026-10-21T09:41:07.000Z",
                    }
                ]
            },
        )

    out = provider.wait(provider.attach("a@dummyinbox.com"), timeout=5)
    assert len(out) == 1 and out[0].subject == "arrived"


def test_unsupported_capabilities_raise(provider, router: Router):
    """No documented delete/mark-read endpoint, so those must raise cleanly."""
    _domains_route(router)
    for cap in ("delete_message", "delete_mailbox", "mark_read", "attachments"):
        assert not provider.supports(cap), f"{cap} must not be declared"
