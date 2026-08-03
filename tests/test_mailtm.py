"""Tests for the mail.tm provider (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from multitempmail import get_provider
from multitempmail.errors import AuthenticationError, MailboxExpired
from tests.conftest import Router, json_response, make_http, request_body

DOMAINS = {
    "hydra:member": [
        {"id": "1", "domain": "private.one", "isActive": True, "isPrivate": True},
        {"id": "2", "domain": "public.two", "isActive": True, "isPrivate": False},
        {"id": "3", "domain": "inactive.three", "isActive": False},
    ],
    "hydra:totalItems": 3,
}


@pytest.fixture
def provider(router: Router):
    return get_provider("mailtm", http=make_http("https://api.mail.tm", router))


def test_create_picks_public_active_domain(router: Router, provider):
    created = {"id": "acc-1", "address": "user@public.two", "createdAt": "2024-01-02T03:04:05Z"}
    calls = {}

    @router.on("GET", "/domains")
    def domains(request):
        return json_response(200, DOMAINS, request)

    @router.on("POST", "/accounts")
    def accounts(request):
        body = request_body(request)
        calls["account"] = body
        return json_response(201, created, request)

    @router.on("POST", "/token")
    def token(request):
        calls["token"] = request_body(request)
        return json_response(201, {"id": "acc-1", "token": "tok-1"}, request)

    mailbox = provider.create(local_part="user")
    assert mailbox.address == "user@public.two"
    assert mailbox.token == "tok-1"
    assert mailbox.password
    assert calls["account"]["password"] == mailbox.password
    assert calls["token"] == {
        "address": "user@public.two",
        "password": mailbox.password,
    }
    assert mailbox.raw["account_id"] == "acc-1"


def test_create_respects_explicit_domain(router: Router, provider):
    @router.on("GET", "/domains")
    def domains(request):
        return json_response(200, DOMAINS, request)

    @router.on("POST", "/accounts")
    def accounts(request):
        return json_response(201, {"id": "a", "address": request_body(request)["address"]}, request)

    @router.on("POST", "/token")
    def token(request):
        return json_response(201, {"token": "t"}, request)

    mailbox = provider.create(domain="custom.example", local_part="me")
    assert mailbox.address == "me@custom.example"


def test_create_no_active_domains(router: Router, provider):
    @router.on("GET", "/domains")
    def domains(request):
        return json_response(200, {"hydra:member": [{"domain": "x", "isActive": False}]}, request)

    with pytest.raises(Exception, match="no active domains"):
        provider.create()


def test_attach_with_token(provider):
    mailbox = provider.attach("u@public.two", token="tok-2", account_id="acc-9")
    assert mailbox.token == "tok-2"
    assert mailbox.raw["account_id"] == "acc-9"


def test_attach_with_password_reauthenticates(router: Router, provider):
    @router.on("POST", "/token")
    def token(request):
        assert request_body(request) == {"address": "u@public.two", "password": "pw"}
        return json_response(201, {"id": "acc-5", "token": "fresh"}, request)

    mailbox = provider.attach("u@public.two", password="pw")
    assert mailbox.token == "fresh"
    assert mailbox.raw["account_id"] == "acc-5"


def test_attach_requires_credential(provider):
    with pytest.raises(AuthenticationError):
        provider.attach("u@public.two")


def test_messages_paginates(router: Router, provider):
    page1 = [
        {
            "id": 1,
            "from": {"name": "A", "address": "a@x.com"},
            "subject": "one",
            "to": [{"name": None, "address": "u@public.two"}],
            "seen": False,
            "size": 10,
            "createdAt": "2024-01-02T03:04:05Z",
        }
    ] * 30
    page2 = [
        {
            "id": 2,
            "from": "plain@x.com",
            "subject": "two",
            "to": [],
            "seen": True,
            "createdAt": None,
        }
    ]
    seen_pages = []

    @router.on("GET", "/messages")
    def messages(request):
        page = int(request.url.params.get("page", "1"))
        seen_pages.append(page)
        items = page1 if page == 1 else page2
        return json_response(200, {"hydra:member": items}, request)

    mailbox = provider.attach("u@public.two", token="t")
    result = provider.messages(mailbox)
    assert seen_pages == [1, 2]
    assert len(result) == 31
    assert result[0].id == "1" and result[0].sender == "a@x.com"
    assert result[0].to[0].address == "u@public.two"
    assert result[30].sender == "plain@x.com"


def test_message_full(router: Router, provider):
    payload = {
        "id": 7,
        "from": {"name": "B", "address": "b@y.com"},
        "to": [{"name": None, "address": "u@public.two"}],
        "cc": [{"name": None, "address": "c@z.com"}],
        "bcc": [{"name": None, "address": "d@z.com"}],
        "subject": "hi",
        "text": "text body",
        "html": ["<p>", "html", "</p>"],
        "attachments": [
            {
                "id": 9,
                "filename": "f.txt",
                "contentType": "text/plain",
                "size": 5,
                "downloadUrl": "/messages/7/attachments/9",
            }
        ],
    }

    @router.on("GET", "/messages/7")
    def message(request):
        return json_response(200, payload, request)

    mailbox = provider.attach("u@public.two", token="t")
    msg = provider.message(mailbox, "7")
    assert msg.id == "7"
    assert msg.sender == "b@y.com"
    assert msg.body_html == "<p>html</p>"
    assert msg.cc[0].address == "c@z.com"
    assert msg.attachments[0].download_url == "/messages/7/attachments/9"


def test_delete_and_mark_read(router: Router, provider):
    seen = []

    @router.on("DELETE", "/messages/5")
    def delete(request):
        seen.append(("delete", None))
        return httpx.Response(204, request=request)

    @router.on("PATCH", "/messages/5")
    def patch(request):
        seen.append(("patch", request_body(request)))
        return httpx.Response(200, json={"seen": True}, request=request)

    mailbox = provider.attach("u@public.two", token="t")
    provider.delete_message(mailbox, "5")
    provider.mark_read(mailbox, "5")
    assert seen == [("delete", None), ("patch", {"seen": True})]


def test_delete_mailbox_uses_stored_account_id(router: Router, provider):
    @router.on("DELETE", "/accounts/acc-1")
    def delete(request):
        return httpx.Response(204, request=request)

    mailbox = provider.attach("u@public.two", token="t", account_id="acc-1")
    provider.delete_mailbox(mailbox)


def test_delete_mailbox_falls_back_to_me(router: Router, provider):
    @router.on("GET", "/me")
    def me(request):
        return json_response(200, {"id": "acc-2"}, request)

    @router.on("DELETE", "/accounts/acc-2")
    def delete(request):
        return httpx.Response(204, request=request)

    mailbox = provider.attach("u@public.two", token="t")
    provider.delete_mailbox(mailbox)


def test_delete_mailbox_404_becomes_mailbox_expired(router: Router, provider):
    @router.on("DELETE", "/accounts/acc-1")
    def delete(request):
        return httpx.Response(404, request=request)

    mailbox = provider.attach("u@public.two", token="t", account_id="acc-1")
    with pytest.raises(MailboxExpired):
        provider.delete_mailbox(mailbox)


def test_download_attachment_relative_and_absolute(router: Router, provider):
    from multitempmail.models import Attachment

    relative = Attachment(filename="r.txt", download_url="/files/r")
    absolute = Attachment(filename="a.txt", download_url="https://cdn.example.test/a")

    @router.on("GET", "/files/r")
    def rel(request):
        return httpx.Response(200, content=b"rel", request=request)

    @router.on("GET", "/a")
    def abs(request):
        return httpx.Response(200, content=b"abs", request=request)

    mailbox = provider.attach("u@public.two", token="t")
    assert provider.download_attachment(mailbox, "1", relative) == b"rel"
    assert provider.download_attachment(mailbox, "1", absolute) == b"abs"


def test_operations_require_token(provider):
    from multitempmail.models import Mailbox

    mailbox = Mailbox(provider="mailtm", address="u@public.two")
    with pytest.raises(AuthenticationError):
        provider.messages(mailbox)
