# AGENTS.md

Guidance for AI coding agents working on this repository.

## Project Overview

`multitempmail` is a unified Python SDK for
multiple temporary email providers. Users interact with a single API while
the SDK transparently handles provider implementations.

Key features:

- **unified API** — one facade (`TempMailClient`), one mailbox model
- **multiple providers** — mail.tm, catchmail.io, tempmail.lol, mail.cx,
  mail.td (all pluggable)
- **automatic provider failover** — a default client pools every provider
  and fails over on transient errors in priority order
- **provider health management** — failure streaks demote effective
  priority; successful requests restore it
- **waiting utilities** — `wait_for_message` / `wait_for_subject` /
  `wait_for_sender` / `wait_for_otp` / `wait_for_link` / `wait_until`
  (timeout, interval, `stop=` cancellation)
- **OTP extraction** — 6-digit, split (`123-456`), and alphanumeric
  (`ABC123`) patterns with false-positive guards, custom regex support
- **verification link extraction** — first link, `domain=` / `keyword=`
  filters, custom `filter=` callbacks
- **CLI** — `multitempmail create/inbox/read/otp/wait/delete/providers/domains/health`
- **session persistence** — `save_session` / `load_session` /
  `restore_session` (JSON)
- **response cache** — optional TTL-based `ResponseCache`, auto-invalidated
  on write operations
- **provider capability system** — `Cap`-based `supports()` / `require()`

## Architecture

```
multitempmail/
├── client.py        # TempMailClient facade (pool, sessions, delegations)
├── base.py          # BaseProvider, ProviderInfo, registry (register/get_provider)
├── providers/       # one module per provider — provider-specific code ONLY
├── failover.py      # ProviderPool, FailoverError, recoverability rules
├── health.py        # HealthManager / HealthSnapshot
├── cache.py         # ResponseCache (thread-safe, TTL)
├── session.py       # JSON session save/load
├── http.py          # single httpx layer: timeout/retries/proxy/UA/error mapping
├── models.py        # Mailbox (operational), Message, Attachment, Address
├── errors.py        # exception hierarchy, HTTP→exception mapping
├── utils.py         # OTP/link extraction, random address helpers, parse_dt
└── cli.py           # argparse CLI, thin wrapper over the SDK
```

Rules of the architecture:

- **provider registry** — providers register via the `@register(name)`
  decorator; the client builds pools from `list_providers()`; no hardcoding
  of provider names in client code
- **BaseProvider** — subclasses declare `name`, `display_name`,
  `base_url`, `capabilities`, `default_priority`, `known_domains` and
  implement `create`/`attach`/`messages`/`message`/`delete_*`/`mark_read`/
  `download_attachment`/`wait`; all HTTP goes through `self._request(...)`
- **shared HTTP client** — one `HTTPClient` per provider instance
  (timeouts, idempotent-method retries with backoff, proxy, User-Agent,
  health metrics); providers never build their own transports
- **models** — `Mailbox` is operational (wait helpers, cache
  invalidation, downloads); `Message`/`Attachment`/`Address` are frozen
  dataclasses
- **exceptions** — everything derives from `TempMailError` and carries
  `status_code`/`response`; mapping lives in `errors.error_from_response`
- **CLI** — argparse; delegates to the SDK only (no duplicated logic)
- **utilities** — OTP/link extraction and helpers are pure functions in
  `utils.py`, shared by models and CLI

**Provider-specific logic must remain isolated**: providers/ code may only
contain endpoint calls, payload mapping, and per-provider defaults. Shared
behavior (polling, error mapping, retries, caching, health) lives in the
core modules and must not be re-implemented in a provider.

## How to Add a New Provider

1. Create `multitempmail/providers/<name>.py` with a class inheriting from
   `BaseProvider` (see `mailcx.py` for a compact example).
2. Declare metadata: `name`, `display_name`, `base_url`, `capabilities`
   (`frozenset` of `Cap` values), `default_priority`, `known_domains`.
3. Implement **documented endpoints only** — read the provider's API docs;
   never invent or guess undocumented behavior (e.g. ambiguous response
   shapes are parsed defensively).
4. Register the provider: `from multitempmail import register; @register(...)`.
   Import the module from `multitempmail/providers/__init__.py`.
5. Add tests in `tests/test_<name>.py` using the mocked-HTTP helpers from
   `tests/conftest.py` (`Router`, `json_response`, `make_http`) — no real
   network calls.
6. Update documentation if necessary: README provider list, support
   matrix, and per-provider docs section.

Capabilities are enforced by `require()`; mark unsupported operations with
the provider's actual capabilities so they raise `UnsupportedFeature`
automatically.

## Coding Guidelines

- **never duplicate provider logic** — shared behavior belongs in
  `base.py` / `models.py` / `http.py` / `utils.py`
- **never invent undocumented API behavior** — endpoints, payload shapes
  and error codes must come from provider documentation
- **keep the public API backward compatible** — no signature removals, no
  behavior changes without a deprecation path
- **use type hints** everywhere (mypy-strict on `multitempmail/`)
- **keep tests passing** — `python -m pytest` must stay green
- **use the shared HTTP client** — `self._request(...)` / `HTTPClient`
  only; no bare `httpx` calls in providers
- **follow Ruff, Black and mypy** — `ruff check .`, `black --check .`,
  `mypy multitempmail` must all pass; config lives in `pyproject.toml`
  (line-length 100, py310 target)

## Installation

```bash
pip install multitempmail
```

```python
from multitempmail import TempMailClient
```

## Quick Example

```python
from multitempmail import TempMailClient

client = TempMailClient("mailtm")
mailbox = client.create()
print(mailbox.address)

messages = mailbox.messages()
for message in messages:
    print(message.id, message.subject)

otp = mailbox.wait_for_otp(timeout=120)
print(f"code: {otp}")
```

CLI:

```bash
multitempmail create --provider catchmail --save session.json
multitempmail otp --timeout 60 --session session.json
multitempmail inbox --session session.json
```
