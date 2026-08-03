"""Shared test fixtures: an in-memory HTTP router and helpers to build
providers with mocked HTTP."""

from __future__ import annotations

import httpx
import pytest

from tempmail.http import HTTPClient

RETRY_BACKOFF = 0.01


class Router:
    """Match (method, path) pairs to handler callables.

    Handlers receive the httpx.Request and return an httpx.Response.
    Unexpected requests fail the test with a descriptive error.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, object]] = []

    def on(self, method: str, path: str):
        """Register a handler for an exact method + URL path."""

        def decorator(handler):
            self.routes.append((method, path, handler))
            return handler

        return decorator

    def handler(self, request: httpx.Request) -> httpx.Response:
        for method, path, handler in self.routes:
            if request.method == method and request.url.path == path:
                return handler(request)
        raise AssertionError(
            f"unexpected request: {request.method} {request.url.path}?{request.url.query.decode()}"
        )


def json_response(status: int, payload, request: httpx.Request | None = None, headers=None):
    return httpx.Response(status, json=payload, request=request, headers=headers)


def make_http(base_url: str, router: Router, retries: int = 0) -> HTTPClient:
    transport = httpx.MockTransport(router.handler)
    return HTTPClient(
        base_url=base_url,
        timeout=30.0,
        retries=retries,
        retry_backoff=RETRY_BACKOFF,
        transport=transport,
    )


@pytest.fixture
def router() -> Router:
    return Router()


def request_body(request: httpx.Request) -> dict:
    """JSON body of a request (for assertions)."""
    return __import__("json").loads(request.content or b"{}")
