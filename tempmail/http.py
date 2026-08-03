"""Single HTTP layer shared by every provider.

Built on httpx with sane defaults — timeouts, retries with exponential
backoff for idempotent requests, optional proxy, custom User-Agent — and a
unified mapping of HTTP failures into :mod:`tempmail.errors`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from . import __version__, errors

DEFAULT_USER_AGENT = f"tempmail-sdk/{__version__}"

#: statuses retried with backoff when retries are enabled
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
#: methods safe to retry automatically (non-idempotent writes are not
#: retried to avoid duplicate side effects such as duplicate accounts)
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE", "PUT"})
_BACKOFF_CAP = 10.0


class HTTPClient:
    """Thin, provider-agnostic httpx wrapper.

    Args:
        base_url: base URL that relative request paths are resolved against.
        timeout: default read timeout in seconds.
        retries: automatic retries for idempotent requests that hit a
            retryable status (408/425/429/5xx) or a transport error.
        retry_backoff: base backoff in seconds, doubled after every attempt.
        proxy: optional proxy URL passed through to httpx.
        user_agent: ``User-Agent`` header (defaults to ``tempmail-sdk/x.y.z``).
        headers: extra headers merged into every request.
        transport: optional httpx transport (used by tests to mock HTTP).
        metrics: optional ``(success, elapsed_seconds)`` callback invoked
            once per finished request — used by the health manager.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        proxy: str | None = None,
        user_agent: str | None = None,
        headers: Mapping[str, str] | None = None,
        transport: Any = None,
        metrics: Callable[[bool, float], None] | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self._metrics = metrics
        base_headers = dict(headers or {})
        base_headers.setdefault("User-Agent", user_agent or DEFAULT_USER_AGENT)
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            proxy=proxy,
            headers=base_headers,
            follow_redirects=True,
            transport=transport,
        )
        self._retry_backoff = retry_backoff

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> HTTPClient:  # noqa: PYI034 - returns the managed client
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> httpx.Response:
        """Perform a request and return the response.

        Raises a :class:`tempmail.errors.TempMailError` subclass for
        HTTP >= 400 responses or transport failures.
        """
        timeout = self.timeout if timeout is None else timeout
        attempts = self.retries if retries is None else retries
        retryable = method.upper() in _IDEMPOTENT_METHODS
        backoff = self._retry_backoff
        attempt = 0
        started = time.perf_counter()
        try:
            response = self._attempt(
                method,
                path,
                params,
                json,
                data,
                headers,
                timeout,
                retryable,
                attempts,
                backoff,
                attempt,
            )
        except errors.TempMailError:
            if self._metrics is not None:
                self._metrics(False, time.perf_counter() - started)
            raise
        if self._metrics is not None:
            self._metrics(True, time.perf_counter() - started)
        return response

    def _attempt(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        data: Any,
        headers: Mapping[str, str] | None,
        timeout: float,
        retryable: bool,
        attempts: int,
        backoff: float,
        attempt: int,
    ) -> httpx.Response:
        while True:
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                if retryable and attempt < attempts:
                    self._backoff(backoff)
                    backoff *= 2
                    attempt += 1
                    continue
                raise errors.TimeoutError(
                    f"{method} {path} timed out after {timeout:.0f}s"
                ) from exc
            except httpx.RequestError as exc:
                if retryable and attempt < attempts:
                    self._backoff(backoff)
                    backoff *= 2
                    attempt += 1
                    continue
                raise errors.ProviderError(f"{method} {path} failed: {exc}") from exc
            if response.status_code >= 400:
                if retryable and response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                    self._backoff(self._retry_after(response) or backoff)
                    backoff *= 2
                    attempt += 1
                    continue
                raise errors.error_from_response(response)
            return response

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return min(float(value), _BACKOFF_CAP)
        except ValueError:
            return None

    def _backoff(self, seconds: float) -> None:
        time.sleep(min(seconds, _BACKOFF_CAP))
