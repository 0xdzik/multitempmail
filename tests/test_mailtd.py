"""Tests for the mail.td provider (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from tempmail import get_provider
from tempmail.errors import AuthenticationError, MailboxExpired
from tests.conftest import Router, json_response, make_http, request_body

DOMAINS = {
    "domains": [
        {"id": "d1", "domain": "fallback.td", "default": False},
        {"id": "d2", "domain": "primary.td", "default": True},
    ]
}


@pytest.fixture
def provider(router: Router):
    return get_provider("mailtd", api_key="td_abc", http=make_http("https://api.mail.td", router))


def _assert_auth(request):
    assert request.headers.get("authorization") == "Bearer td_abc"


def test_auth_required_without_api_key():
    provider = get_provider("mailtd")
    with pytest.raises(AuthenticationError):
        provider.create()


def test_domains_public(router: Router):
    provider = get_provider("mailtd", http=make_http("https://api.mail.td", router))

    @router.on("GET", "/api/domains")
    def domains(request):
        return json_response(200, DOMAINS, request)

    assert provider.domains()[0]["domain"] == "fallback.td"


def test_create_prefers_default_domain(router: Router, provider):
    @router.on("GET", "/api/domains")
    def domains(request):
        return json_response(200, DOMAINS, request)

    @router.on("POST", "/api/accounts")
    def accounts(request):
        _assert_auth(request)
        body = request_body(request)
        assert body["address"] == "me@primary.td"
        assert body["password"]
        return json_response(
            201,
            {"id": "acc-uuid", "address": "me@primary.td", "created_at": "2024-01-02T03:04:05Z"},
            request,
        )

    mb = provider.create(local_part="me")
    assert mb.address == "me@primary.td"
    assert mb.token == "acc-uuid"
    assert mb.password


def test_create_explicit_domain(router: Router, provider):
    @router.on("POST", "/api/accounts")
    def accounts(request):
        body = request_body(request)
        return json_response(201, {"id": "u1", "address": body["address"]}, request)

    mb = provider.create(domain="own.td", local_part="x")
    assert mb.address == "x@own.td"


def test_messages_paginates(router: Router, provider):
    page1 = [
        {
            "id": i,
            "from": "s@x.com",
            "sender": "Sender",
            "subject": f"s{i}",
            "is_read": False,
            "size": 10,
            "created_at": None,
        }
        for i in range(30)
    ]
    page2 = [
        {
            "id": 30,
            "from": "t@x.com",
            "subject": "last",
            "is_read": True,
            "created_at": "2024-01-02T03:04:05Z",
        }
    ]
    pages = []

    @router.on("GET", "/api/accounts/acc-uuid/messages")
    def messages(request):
        _assert_auth(request)
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        return json_response(200, {"messages": page1 if page == 1 else page2}, request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    result = provider.messages(mb)
    assert pages == [1, 2]
    assert len(result) == 31
    assert result[0].sender_name == "Sender"
    assert result[30].seen is True


def test_message_full(router: Router, provider):
    payload = {
        "id": "m1",
        "from": "s@x.com",
        "subject": "hi",
        "text_body": "plain",
        "html_body": "<p>plain</p>",
        "is_read": False,
        "size": 9,
        "created_at": "2024-01-02T03:04:05Z",
        "attachments": [{"index": 0, "filename": "f.txt", "content_type": "text/plain", "size": 4}],
    }

    @router.on("GET", "/api/accounts/acc-uuid/messages/m1")
    def message(request):
        _assert_auth(request)
        return json_response(200, payload, request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    msg = provider.message(mb, "m1")
    assert msg.body_text == "plain"
    assert msg.body_html == "<p>plain</p>"
    assert msg.attachments[0].index == 0


def test_mark_read_and_delete(router: Router, provider):
    seen = []

    @router.on("PUT", "/api/accounts/acc-uuid/messages/m1/read")
    def read(request):
        _assert_auth(request)
        seen.append("read")
        return httpx.Response(204, request=request)

    @router.on("DELETE", "/api/accounts/acc-uuid/messages/m1")
    def delete(request):
        _assert_auth(request)
        seen.append("delete")
        return httpx.Response(204, request=request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    provider.mark_read(mb, "m1")
    provider.delete_message(mb, "m1")
    assert seen == ["read", "delete"]


def test_delete_mailbox(router: Router, provider):
    @router.on("DELETE", "/api/accounts/acc-uuid")
    def delete(request):
        _assert_auth(request)
        return httpx.Response(204, request=request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    provider.delete_mailbox(mb)


def test_delete_mailbox_404_becomes_mailbox_expired(router: Router, provider):
    @router.on("DELETE", "/api/accounts/acc-uuid")
    def delete(request):
        return httpx.Response(404, request=request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    with pytest.raises(MailboxExpired):
        provider.delete_mailbox(mb)


def test_download_attachment_by_index(router: Router, provider):
    from tempmail.models import Attachment

    @router.on("GET", "/api/accounts/acc-uuid/messages/m1/attachments/2")
    def attachment(request):
        _assert_auth(request)
        return httpx.Response(200, content=b"data", request=request)

    mb = provider.attach("me@primary.td", token="acc-uuid")
    att = Attachment(filename="f", index=2)
    assert provider.download_attachment(mb, "m1", att) == b"data"
