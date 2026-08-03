"""Tests for the catchmail.io provider (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest

from multitempmail import get_provider
from multitempmail.errors import UnsupportedFeature
from tests.conftest import Router, json_response, make_http


@pytest.fixture
def provider(router: Router):
    return get_provider("catchmail", http=make_http("https://api.catchmail.io", router))


def test_create_makes_no_request(provider):
    mailbox = provider.create()
    assert mailbox.address.endswith("@catchmail.io")
    assert mailbox.address.split("@")[0]


def test_create_custom_domain_and_local_part(provider):
    mailbox = provider.create(domain="my.example", local_part="foo")
    assert mailbox.address == "foo@my.example"


def test_messages(router: Router, provider):
    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        assert request.url.params.get("address") == "a@catchmail.io"
        return json_response(
            200,
            {
                "address": "a@catchmail.io",
                "count": 2,
                "messages": [
                    {
                        "id": "m1",
                        "from": "x@y.z",
                        "subject": "one",
                        "size": 3,
                        "date": "2024-01-02T03:04:05Z",
                    },
                    {"id": "m2", "from": None, "subject": None, "date": None},
                ],
            },
            request,
        )

    mb = provider.attach("a@catchmail.io")
    msgs = provider.messages(mb)
    assert [m.id for m in msgs] == ["m1", "m2"]
    assert msgs[0].sender == "x@y.z"
    assert msgs[0].created_at is not None
    assert msgs[1].created_at is None


def test_message_full(router: Router, provider):
    payload = {
        "id": "m1",
        "from": "x@y.z",
        "to": ["a@catchmail.io"],
        "subject": "hi",
        "date": "2024-01-02T03:04:05Z",
        "size": 42,
        "body": {"text": "plain", "html": "<b>plain</b>"},
        "attachments": [
            {
                "id": "at1",
                "filename": "f.txt",
                "content_type": "text/plain",
                "size": 5,
                "download_url": "/files/at1",
            }
        ],
    }

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        assert request.url.params.get("mailbox") == "a@catchmail.io"
        return json_response(200, payload, request)

    mb = provider.attach("a@catchmail.io")
    msg = provider.message(mb, "m1")
    assert msg.subject == "hi"
    assert msg.body_text == "plain"
    assert msg.body_html == "<b>plain</b>"
    assert msg.to[0].address == "a@catchmail.io"
    assert msg.attachments[0].download_url == "/files/at1"


def test_delete_message(router: Router, provider):
    @router.on("DELETE", "/api/v1/message/m1")
    def delete(request):
        assert request.url.params.get("mailbox") == "a@catchmail.io"
        return httpx.Response(204, request=request)

    provider.delete_message(provider.attach("a@catchmail.io"), "m1")


def test_download_attachment(router: Router, provider):
    from multitempmail.models import Attachment

    @router.on("GET", "/files/at1")
    def file(request):
        return httpx.Response(200, content=b"data", request=request)

    mb = provider.attach("a@catchmail.io")
    att = Attachment(filename="f.txt", download_url="/files/at1")
    assert provider.download_attachment(mb, "m1", att) == b"data"


def test_unsupported_operations(provider):
    mb = provider.attach("a@catchmail.io")
    with pytest.raises(UnsupportedFeature):
        provider.delete_mailbox(mb)
    with pytest.raises(UnsupportedFeature):
        provider.mark_read(mb, "m1")
