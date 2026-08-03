"""Tests for provider failover and the health-driven pool ordering."""

from __future__ import annotations

import pytest
from conftest import Router, json_response, make_http

from multitempmail.base import get_provider
from multitempmail.errors import AuthenticationError, TempMailError
from multitempmail.failover import FailoverError, ProviderPool
from multitempmail.health import HealthManager

_BASES = {
    "mailtm": "https://api.mail.tm",
    "mailcx": "https://api.mail.cx/v1",
    "catchmail": "https://api.catchmail.io",
    "mailtd": "https://api.mail.td",
}


def _provider(name: str, router: Router) -> object:
    return get_provider(name, http=make_http(_BASES[name], router))


def _pool(*providers: object, health: HealthManager | None = None) -> ProviderPool:
    return ProviderPool(list(providers), health=health)  # type: ignore[arg-type]


def test_failover_tries_next_provider_on_rate_limit() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(429, {"error": "rate limit"})

    mailcx_router = Router()

    @mailcx_router.on("GET", "/v1/config")
    def config(request):
        return json_response(
            200,
            {"system_domains": [{"domain": "cx.io", "default": True}], "local_part_rules": {}},
        )

    health = HealthManager()
    pool = _pool(
        _provider("mailtm", mailtm_router), _provider("mailcx", mailcx_router), health=health
    )
    mailbox = pool.create()
    assert mailbox.address.endswith("@cx.io")
    assert health.snapshot("mailtm", 100).consecutive_failures == 1
    assert health.is_healthy("mailcx")


def test_permanent_error_stops_and_raises() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(400, {"error": "invalid request"})

    mailcx_router = Router()

    @mailcx_router.on("GET", "/v1/config")
    def config(request):
        raise AssertionError("mailcx must not be tried after a permanent error")

    pool = _pool(_provider("mailtm", mailtm_router), _provider("mailcx", mailcx_router))
    with pytest.raises(FailoverError) as excinfo:
        pool.create()
    assert "permanent error" in str(excinfo.value)
    assert [name for name, _ in excinfo.value.failures] == ["mailtm"]
    assert isinstance(excinfo.value.failures[0][1], TempMailError)


def test_all_providers_failed_lists_every_failure() -> None:
    def failing(router: Router, path: str) -> None:
        @router.on("GET", path)
        def handler(request, status=503):
            return json_response(status, {"error": "boom"})

    mailtm_router = Router()
    failing(mailtm_router, "/domains")
    mailcx_router = Router()
    failing(mailcx_router, "/v1/config")
    mailtd_router = Router()
    failing(mailtd_router, "/api/domains")
    pool = _pool(
        _provider("mailtm", mailtm_router),
        _provider("mailcx", mailcx_router),
        get_provider(
            "mailtd",
            http=make_http(_BASES["mailtd"], mailtd_router),
            api_key="td_x",
        ),
    )
    with pytest.raises(FailoverError) as excinfo:
        pool.create()
    assert len(excinfo.value.failures) == 3
    assert [name for name, _ in excinfo.value.failures] == ["mailtm", "mailcx", "mailtd"]


def test_attach_skips_authentication_errors() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        raise AssertionError("unexpected request")

    mailcx_router = Router()
    pool = _pool(_provider("mailtm", mailtm_router), _provider("mailcx", mailcx_router))
    mailbox = pool.attach("user@cx.io")
    assert mailbox.address == "user@cx.io"
    assert mailbox.provider == "mailcx"


def test_attach_all_auth_failures_raises() -> None:
    mailtm_router = Router()
    pool = _pool(_provider("mailtm", mailtm_router))
    with pytest.raises(FailoverError) as excinfo:
        pool.attach("user@mail.tm")
    assert excinfo.value.failures[0][0] == "mailtm"
    assert isinstance(excinfo.value.failures[0][1], AuthenticationError)


def test_demoted_provider_tried_last() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    pool = _pool(
        _provider("mailtm", Router()),
        _provider("mailcx", Router()),
        _provider("catchmail", Router()),
        health=health,
    )
    assert [p.name for p in pool.ordered()] == ["mailtm", "mailcx", "catchmail"]
    health.record_failure("mailtm")
    health.record_failure("mailtm")
    assert [p.name for p in pool.ordered()] == ["mailcx", "catchmail", "mailtm"]


def test_success_recovers_ordering() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    pool = _pool(
        _provider("mailtm", Router()),
        _provider("mailcx", Router()),
        health=health,
    )
    health.record_failure("mailtm")
    health.record_failure("mailtm")
    assert pool.ordered()[0].name == "mailcx"
    health.record_success("mailtm")
    assert pool.ordered()[0].name == "mailtm"


def test_client_pool_default_orders_by_priority() -> None:
    from multitempmail.client import TempMailClient

    client = TempMailClient()
    assert client.name == "mailtm"
    assert [p.name for p in client.providers()] == [
        "mailtm",
        "mailcx",
        "catchmail",
        "tempmail_lol",
        "mailtd",
    ]


def test_client_create_fails_over_with_mocked_http() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(429, {"error": "rate limit"})

    mailcx_router = Router()

    @mailcx_router.on("GET", "/v1/config")
    def config(request):
        return json_response(
            200,
            {"system_domains": [{"domain": "cx.io", "default": True}], "local_part_rules": {}},
        )

    from multitempmail.client import TempMailClient

    client = TempMailClient(
        [
            _provider("mailtm", mailtm_router),
            _provider("mailcx", mailcx_router),
        ]
    )
    mailbox = client.create()
    assert mailbox.address.endswith("@cx.io")
    assert client.active is mailbox
    assert client.health()[0].name == "mailtm"
    assert client.health()[0].consecutive_failures == 1


def test_http_metrics_report_health() -> None:
    router = Router()

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        return json_response(200, {"messages": []})

    health = HealthManager()
    http = make_http("https://api.catchmail.io", router)
    http._metrics = lambda ok, elapsed: (
        health.record_success("catchmail", elapsed) if ok else health.record_failure("catchmail")
    )
    provider = get_provider("catchmail", http=http)
    provider.create().messages()
    assert health.snapshot("catchmail", 90).consecutive_successes == 1
    assert health.snapshot("catchmail", 90).total_requests == 1


def test_base_provider_wires_metrics_when_no_http() -> None:
    health = HealthManager()
    provider = get_provider("catchmail", health=health)
    provider._http_metrics(True, 0.05)
    provider._http_metrics(False, 0.1)
    snapshot = health.snapshot("catchmail", 90)
    assert snapshot.total_requests == 2
    assert snapshot.consecutive_failures == 1
    assert snapshot.consecutive_successes == 0
    assert snapshot.consecutive_failures == 1


def test_metrics_recorded_on_http_failure() -> None:
    router = Router()

    @router.on("GET", "/api/v1/mailbox")
    def mailbox(request):
        return json_response(500, {"error": "boom"})

    health = HealthManager()
    provider = get_provider(
        "catchmail",
        http=make_http("https://api.catchmail.io", router),
        health=health,
    )
    provider.http._metrics = lambda ok, elapsed: (
        health.record_success("catchmail", elapsed) if ok else health.record_failure("catchmail")
    )
    with pytest.raises(TempMailError):
        provider.create().messages()
    assert health.snapshot("catchmail", 90).consecutive_failures == 1
