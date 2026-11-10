"""Tests for provider failover and the health-driven pool ordering.

Only key-free providers exist now: mailtm, catchmail, tempmail_lol.
"""

from __future__ import annotations

import pytest
from conftest import Router, json_response, make_http

from multitempmail.base import get_provider
from multitempmail.errors import AuthenticationError, TempMailError
from multitempmail.failover import FailoverError, ProviderPool
from multitempmail.health import HealthManager

_BASES = {
    "mailtm": "https://api.mail.tm",
    "catchmail": "https://api.catchmail.io",
    "tempmail_lol": "https://api.tempmail.lol",
}


def _provider(name: str, router: Router) -> object:
    return get_provider(name, http=make_http(_BASES[name], router))


def _pool(*providers: object, health: HealthManager | None = None) -> ProviderPool:
    return ProviderPool(list(providers), health=health)  # type: ignore[arg-type]


def _lol_create(router: Router, address: str = "x@lol.test") -> None:
    """tempmail.lol makes its address over POST /v3/inboxes."""

    @router.on("POST", "/v3/inboxes")
    def create(request):
        return json_response(200, {"address": address, "token": "tok_lol"})


def test_failover_tries_next_provider_on_rate_limit() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(429, {"error": "rate limit"})

    lol_router = Router()
    _lol_create(lol_router, "fallback@lol.test")

    health = HealthManager()
    pool = _pool(
        _provider("mailtm", mailtm_router),
        _provider("tempmail_lol", lol_router),
        health=health,
    )
    mailbox = pool.create()
    assert mailbox.address == "fallback@lol.test"
    assert health.snapshot("mailtm", 100).consecutive_failures == 1
    assert health.is_healthy("tempmail_lol")


def test_permanent_error_stops_and_raises() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(400, {"error": "invalid request"})

    lol_router = Router()

    @lol_router.on("POST", "/v3/inboxes")
    def create(request):
        raise AssertionError("tempmail_lol must not be tried after a permanent error")

    pool = _pool(_provider("mailtm", mailtm_router), _provider("tempmail_lol", lol_router))
    with pytest.raises(FailoverError) as excinfo:
        pool.create()
    assert "permanent error" in str(excinfo.value)
    assert [name for name, _ in excinfo.value.failures] == ["mailtm"]
    assert isinstance(excinfo.value.failures[0][1], TempMailError)


def test_all_providers_failed_lists_every_failure() -> None:
    def failing(router: Router, path: str, method: str = "GET") -> None:
        @router.on(method, path)
        def handler(request):
            return json_response(503, {"error": "boom"})

    mailtm_router = Router()
    failing(mailtm_router, "/domains")
    lol_router = Router()
    failing(lol_router, "/v3/inboxes", "POST")

    pool = _pool(
        _provider("mailtm", mailtm_router),
        _provider("tempmail_lol", lol_router),
    )

    with pytest.raises(FailoverError) as excinfo:
        pool.create()
    assert len(excinfo.value.failures) == 2
    assert [name for name, _ in excinfo.value.failures] == ["mailtm", "tempmail_lol"]


def test_catchmail_create_makes_no_request() -> None:
    """catchmail generates the address locally — it can never fail at create()."""

    @Router().on("GET", "/api/v1/mailbox")
    def unused(request):  # pragma: no cover - must never be called
        raise AssertionError("catchmail.create() must not perform a request")

    router = Router()
    router._call = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network hit"))
    pool = _pool(_provider("catchmail", router))
    mailbox = pool.create()
    assert mailbox.address.endswith("@catchmail.io")
    assert mailbox.provider == "catchmail"


def test_attach_skips_authentication_errors() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        raise AssertionError("unexpected request")

    catchmail_router = Router()
    pool = _pool(_provider("mailtm", mailtm_router), _provider("catchmail", catchmail_router))
    mailbox = pool.attach("user@catchmail.io")
    assert mailbox.address == "user@catchmail.io"
    assert mailbox.provider == "catchmail"


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
        _provider("tempmail_lol", Router()),
        _provider("catchmail", Router()),
        health=health,
    )
    assert [p.name for p in pool.ordered()] == ["catchmail", "mailtm", "tempmail_lol"]
    health.record_failure("catchmail")
    health.record_failure("catchmail")
    assert [p.name for p in pool.ordered()] == ["mailtm", "tempmail_lol", "catchmail"]


def test_success_recovers_ordering() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    pool = _pool(
        _provider("mailtm", Router()),
        _provider("tempmail_lol", Router()),
        health=health,
    )
    health.record_failure("mailtm")
    health.record_failure("mailtm")
    assert pool.ordered()[0].name == "tempmail_lol"
    health.record_success("mailtm")
    assert pool.ordered()[0].name == "mailtm"


def test_client_pool_default_orders_by_priority() -> None:
    from multitempmail.client import TempMailClient

    client = TempMailClient()
    assert client.name == "catchmail"
    assert [p.name for p in client.providers()] == [
        "catchmail",
        "mailtm",
        "tempmail_lol",
    ]


def test_client_create_fails_over_with_mocked_http() -> None:
    mailtm_router = Router()

    @mailtm_router.on("GET", "/domains")
    def domains(request):
        return json_response(429, {"error": "rate limit"})

    lol_router = Router()
    _lol_create(lol_router, "second@lol.test")

    from multitempmail.client import TempMailClient

    client = TempMailClient(
        [
            _provider("mailtm", mailtm_router),
            _provider("tempmail_lol", lol_router),
        ]
    )
    mailbox = client.create()
    assert mailbox.address == "second@lol.test"
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
