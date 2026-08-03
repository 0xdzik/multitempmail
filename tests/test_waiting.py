"""Tests for the waiting utilities (Mailbox.wait_for_*)."""

from __future__ import annotations

import pytest

from multitempmail import get_provider
from multitempmail.errors import TimeoutError
from tests.conftest import Router, json_response, make_http


@pytest.fixture
def polling(router: Router):
    """catchmail.io: no server-side wait → client-side polling."""
    return get_provider("catchmail", http=make_http("https://api.catchmail.io", router))


@pytest.fixture
def server_wait(router: Router):
    """tempmail.lol: server-side long-poll."""
    return get_provider("tempmail_lol", http=make_http("https://api.tempmail.lol", router))


def test_wait_for_message_polls_for_new_mail(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] <= 2:
            return json_response(200, {"messages": []}, request)
        return json_response(
            200,
            {"messages": [{"id": "m9", "from": "s@x.com", "subject": "fresh"}]},
            request,
        )

    msg = mb.wait_for_message(timeout=2, interval=0.01)
    assert msg.id == "m9"


def test_wait_for_message_ignores_existing_mail(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": [{"id": "old", "from": "x@y.z"}]}, request)
        if state["count"] == 2:
            return json_response(200, {"messages": [{"id": "old"}, {"id": "new"}]}, request)
        return json_response(200, {"messages": []}, request)

    msg = mb.wait_for_message(timeout=2, interval=0.01)
    assert msg.id == "new"


def _messages_after(router: Router, first: list[dict], second: list[dict]):
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": first}, request)
        return json_response(200, {"messages": second}, request)

    return mailbox


def test_wait_for_subject_filter(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    _messages_after(
        router,
        [],
        [
            {"id": "m1", "from": "bot@x.com", "subject": "Verify your account"},
            {"id": "m2", "from": "noreply@y.com", "subject": "Another thing"},
        ],
    )
    msg = mb.wait_for_subject("verify", timeout=2, interval=0.01)
    assert msg.id == "m1"


def test_wait_for_sender_filter(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    _messages_after(
        router,
        [],
        [
            {"id": "m1", "from": "bot@x.com", "subject": "Verify your account"},
            {"id": "m2", "from": "noreply@y.com", "subject": "Another thing"},
        ],
    )
    msg = mb.wait_for_sender("noreply@y.com", timeout=2, interval=0.01)
    assert msg.id == "m2"


def test_wait_for_otp_fetches_full_body(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": []}, request)
        return json_response(200, {"messages": [{"id": "m1", "from": "x@y.z"}]}, request)

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        return json_response(
            200,
            {"id": "m1", "body": {"text": "Your code is 123-456"}, "attachments": []},
            request,
        )

    assert mb.wait_for_otp(timeout=2, interval=0.01) == "123456"


def test_wait_for_otp_custom_pattern(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": []}, request)
        return json_response(200, {"messages": [{"id": "m1"}]}, request)

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        return json_response(
            200, {"id": "m1", "body": {"text": "code 9876"}, "attachments": []}, request
        )

    assert mb.wait_for_otp(pattern=r"\d{4}", timeout=2, interval=0.01) == "9876"


def test_wait_for_link(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": []}, request)
        return json_response(200, {"messages": [{"id": "m1"}]}, request)

    @router.on("GET", "/api/v1/message/m1")
    def message(request):
        return json_response(
            200,
            {"id": "m1", "body": {"text": "Click https://example.com/reset"}, "attachments": []},
            request,
        )

    assert mb.wait_for_link(domain="example.com", timeout=2, interval=0.01) == (
        "https://example.com/reset"
    )


def test_wait_until_custom_predicate(router: Router, polling):
    mb = polling.attach("a@catchmail.io")
    state = {"count": 0}

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        state["count"] += 1
        if state["count"] == 1:
            return json_response(200, {"messages": []}, request)
        return json_response(200, {"messages": [{"id": "m1", "subject": "SPECIAL"}]}, request)

    result = mb.wait_until(lambda msgs: len(msgs) if msgs else None, timeout=2, interval=0.01)
    assert result == 1


def test_wait_times_out(router: Router, polling):
    mb = polling.attach("a@catchmail.io")

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        return json_response(200, {"messages": []}, request)

    with pytest.raises(TimeoutError):
        mb.wait_for_message(timeout=0.2, interval=0.01)


def test_server_wait_provider_uses_provider_wait(router: Router, server_wait):
    mb = server_wait.attach("u@tmail.wtf", token="tok-1")
    calls = []

    @router.on("GET", "/v3/inboxes/tok-1/wait")
    def wait(request):
        calls.append(int(request.url.params.get("timeout", "0")))
        if len(calls) == 1:
            return json_response(200, {"emails": [], "timed_out": True}, request)
        return json_response(
            200,
            {"emails": [{"id": "e5", "from": "s@x.com", "subject": "arrived", "body": "b"}]},
            request,
        )

    msg = mb.wait_for_message(timeout=5, interval=0.01)
    assert msg.id == "e5"
    assert len(calls) == 2
    assert calls[1] <= calls[0]


def test_server_wait_provider_otp_directly(router: Router, server_wait):
    mb = server_wait.attach("u@tmail.wtf", token="tok-1")

    @router.on("GET", "/v3/inboxes/tok-1/wait")
    def wait(request):
        return json_response(
            200,
            {"emails": [{"id": "e6", "subject": "code", "body": "Your OTP: 555555"}]},
            request,
        )

    assert mb.wait_for_otp(timeout=5, interval=0.01) == "555555"


def test_wait_until_timeout_raises(router: Router, polling):
    mb = polling.attach("a@catchmail.io")

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        return json_response(200, {"messages": []}, request)

    with pytest.raises(TimeoutError):
        mb.wait_until(lambda msgs: None, timeout=0.2, interval=0.01)
