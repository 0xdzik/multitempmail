# multitempmail

[![CI](https://github.com/0xdzik/multitempmail/actions/workflows/ci.yml/badge.svg)](https://github.com/0xdzik/multitempmail/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/multitempmail.svg)](https://pypi.org/project/multitempmail/)
[![Python](https://img.shields.io/pypi/pyversions/multitempmail.svg)](https://pypi.org/project/multitempmail/)
[![License](https://img.shields.io/pypi/l/multitempmail.svg)](https://github.com/0xdzik/multitempmail/blob/main/LICENSE)

A production-oriented Python SDK for temporary email providers: one
consistent API, five providers, no per-provider boilerplate.

## Project Overview

`multitempmail` is a unified Python SDK that lets you use temporary email
providers through a single API. You interact with one facade
(`TempMailClient`) and one mailbox model; the SDK transparently handles
the provider behind the scenes — including automatic failover when a
provider is down or rate-limited.

Highlights:

- **one consistent API** — same models, exceptions and helpers for every
  provider
- **multiple providers** — mail.tm, catchmail.io, tempmail.lol, mail.cx,
  mail.td, all pluggable behind a dynamic registry
- **automatic provider failover** — the default client pools every
  provider and falls through on transient errors in priority order
- **provider health management** — failure streaks demote a provider
  temporarily; successful requests restore it
- **waiting utilities** — `wait_for_message` / `wait_for_subject` /
  `wait_for_sender` / `wait_for_otp` / `wait_for_link` / `wait_until`,
  with timeout, polling interval and `stop=` cancellation
- **OTP extraction** — 6-digit, split (`123-456`) and alphanumeric
  (`ABC123`) codes with false-positive guards and custom regex support
- **verification link extraction** — first link, `domain=` / `keyword=`
  filters and custom `filter=` callbacks
- **response cache** — optional TTL-based in-memory cache,
  auto-invalidated on write operations
- **session persistence** — save and restore your client and mailbox as
  JSON
- **CLI** — the `multitempmail` command for the most common
  operations, no extra dependencies

The provider list:

- **mail.tm** — accounts + Bearer tokens, domains, attachments, mark-read.
- **catchmail.io** — no auth, no setup: mail starts buffering at any
  `@catchmail.io` (or custom MX) address.
- **tempmail.lol** — v3 API: inboxes, consuming fetch, server-side
  long-poll (`/wait`).
- **mail.cx** — implicit mailboxes, 25s server long-poll, cursor-based
  incremental reads, attachment downloads by index.
- **mail.td** — Pro API token required; accounts, paginated messages,
  mark-read, attachments.

Every provider is pluggable behind the same `TempMailClient` facade with a
shared HTTP layer (timeouts, retries, proxy, User-Agent), normalized
models, a capability system and one consistent error hierarchy.

## Install

```bash
pip install multitempmail
pip install -e .[dev]               # from this checkout
```

The distribution name and import namespace are one and the same:
`pip install multitempmail` then `from multitempmail import TempMailClient`.

```python
from multitempmail import TempMailClient
```

Requires Python ≥ 3.10. Runtime dependency: `httpx` only.

## Quick Start

Create a mailbox, read messages and wait for a one-time code:

```python
from multitempmail import TempMailClient

client = TempMailClient("mailtm")          # default provider is mail.tm
mailbox = client.create()

print(mailbox.address)                      # e.g. k9f2x7q1@some-domain.tm

messages = mailbox.messages()               # list inbox (summary fields)
for message in messages:
    print(message.id, message.subject)

full = mailbox.message(messages[0].id)      # read one full message
print(full.body_text)

otp = mailbox.wait_for_otp(timeout=120)     # waits for a new message with a code
print(otp)                                  # e.g. 483920
```

Or use the CLI for the same flow:

```bash
multitempmail create --provider catchmail --save session.json
multitempmail inbox --session session.json
multitempmail otp --timeout 60 --session session.json
```

## Providers

```python
# catchmail.io — nothing to configure, mailbox is implicit
client = TempMailClient("catchmail")
mailbox = client.create()                   # local address, no API call

# tempmail.lol — optional Plus/Ultra API key
client = TempMailClient("tempmail.lol", api_key="tl_...")
mailbox = client.create(prefix="myapp")

# mail.cx — optional token for higher rate limits
client = TempMailClient("mail.cx", api_token="tm_live_...")
mailbox = client.create()                   # picks a system domain from /v1/config

# mail.td — API token required (https://mail.td/pro)
client = TempMailClient("mail.td", api_key="td_...")
mailbox = client.create()
```

Provider names are case-insensitive; `mail.tm`, `mail.cx`, `mail.td`,
`tempmail.lol` and `catchmail.io` aliases work everywhere.

### Provider support matrix

| Provider    | create | list | read | delete msg | delete mailbox | mark read | attachments | server wait | domain select |
|-------------|:------:|:----:|:----:|:----------:|:--------------:|:---------:|:-----------:|:-----------:|:-------------:|
| mailtm      |   ✓    |  ✓   |  ✓   |     ✓      |       ✓        |     ✓     |      ✓      |             |       ✓       |
| catchmail   |   ✓    |  ✓   |  ✓   |     ✓      |                |           |      ✓      |             |               |
| tempmail_lol|   ✓    |  ✓   |      |     ✓      |       ✓        |           |             |      ✓      |               |
| mailcx      |   ✓    |  ✓   |  ✓   |     ✓      |       ✓*       |           |      ✓      |      ✓      |               |
| mailtd      |   ✓    |  ✓   |  ✓   |     ✓      |       ✓        |     ✓     |      ✓      |             |       ✓       |

`*` mail.cx has no mailbox deletion; `delete_mailbox()` clears every email
at the address (documented behavior).

Unsupported operations raise `UnsupportedFeature` (a `TempMailError`).

## Provider Selection

### Default: automatic failover

`TempMailClient()` with no provider builds a **pool of every registered
provider** and tries them in order of *effective priority*:

```python
client = TempMailClient()          # every provider, priority order
mailbox = client.create()          # first provider that succeeds
```

| Provider    | priority | notes                              |
|-------------|:--------:|------------------------------------|
| mailtm      |   100    | default, tried first               |
| mailcx      |    95    |                                    |
| catchmail   |    90    | no setup — good failover fallback  |
| tempmail_lol|    85    |                                    |
| mailtd      |    60    | needs an API key                   |

### Selecting a provider explicitly

Pass a single name to use **only** that provider (identical to the classic
single-provider behavior), or a list to opt into a custom set:

```python
client = TempMailClient("mailtm")            # exactly one provider
client = TempMailClient(["mailtm", "mail.cx"])   # just these two
```

Provider names are case-insensitive; `mail.tm`, `mail.cx`, `mail.td`,
`tempmail.lol` and `catchmail.io` aliases work everywhere.

### How priority works

Providers are tried highest **effective priority** first. `priority` is
static; the health manager adjusts it live: every recorded failure lowers
it by 20 points per consecutive failure (capped), and one successful
`create()` restores the provider to its full base priority.

### How health affects selection

Failover rules:

- **Recoverable failures** (rate limit, timeout, transport error, 5xx)
  advance to the next provider.
- **Permanent errors** (invalid parameters, bad credentials, 404/410...)
  raise `FailoverError` immediately — providers are never retried on
  problems retrying can't fix.
- `client.providers()` lists static metadata (priority, capabilities,
  aliases, known domains); `client.health()` shows live health per
  provider (failure streaks, average response time, effective priority).

Providers without credentials fail over automatically (e.g. mail.td skips
its `AuthenticationError`; a mail.td pool with no `api_key` just moves on).

## Response cache

Optional in-memory cache (TTL-based, thread-safe):

```python
from multitempmail import ResponseCache

client = TempMailClient(cache=ResponseCache(default_ttl=30))
messages = mailbox.messages()      # cached for 30s
mailbox.delete(messages[0].id)     # write ops invalidate the cache
```

Message lists are cached per mailbox and auto-invalidated on
`delete()` / `mark_read()` / `delete_mailbox()`. Domain listings are
cached per provider. Waiting helpers bypass the cache entirely, so
`wait_for_*` never misses new mail. Providers whose fetch consumes mail
(tempmail.lol) never cache. `provider.cache` / `cache_messages=False`
disable it per provider; `client.cache=None` (default) turns it off.

## Sessions

Persist and restore a client (provider pool, options, active mailbox):

```python
mailbox = client.create()
client.save_session("session.json")

restored = TempMailClient.load_session("session.json")
mailbox = restored.active          # usable again, e.g. mail.tm token
restored.wait_for_otp(timeout=60)
```

Sessions are JSON files with exactly what is needed to restore the
mailbox (provider names, jsonable options, address/token/password).
`restore_session()` applies a session to an existing client in place.

## CLI

The package ships a `multitempmail` console command (no extra
dependencies):

```bash
multitempmail create --provider catchmail --save session.json
multitempmail inbox --session session.json
multitempmail read <message-id> --session session.json
multitempmail otp --timeout 60 --session session.json
multitempmail wait --subject confirm --session session.json
multitempmail wait --otp --timeout 60 --session session.json
multitempmail delete <message-id> --session session.json
multitempmail providers --json
multitempmail domains --provider mailtm --json
multitempmail health --json
```

`create` writes a session file (default `session.json`); the other
commands load it, creating a fresh mailbox on demand if none was saved.
Everything the CLI does is a thin wrapper over the SDK — no duplicated
logic.

## Wait helpers

All helpers wait **only for mail that arrives after the call**, and raise
`TimeoutError` (from `multitempmail.errors`) on expiry. Providers with a
server-side long-poll (tempmail.lol `/wait`, mail.cx 25s hold) use it;
others poll at `interval` seconds (default 2s).

```python
mailbox.wait_for_message(timeout=60)                     # first new message
mailbox.wait_for_subject("confirm", timeout=60)          # subject contains ...
mailbox.wait_for_sender("noreply@example.com")           # sender contains ...
mailbox.wait_for_otp(timeout=120)                        # extract OTP from body
mailbox.wait_for_otp(pattern=r"\d{8}")                   # custom code shape
mailbox.wait_for_link(domain="example.com", keyword="reset")
mailbox.wait_until(lambda msgs: msgs and len(msgs) >= 2) # custom predicate

stop = lambda: stop_event.is_set()                       # cancel from another thread
try:
    otp = mailbox.wait_for_otp(timeout=120, stop=stop)
except WaitCancelled:                                    # stop() returned True
    ...
```

Cancellation: every helper takes a `stop` callable checked between
polling cycles (and after each server-side long-poll); returning `True`
raises `WaitCancelled` (a `TimeoutError` subclass).

OTP extraction tries, in order: 6 digits (`123456`), 6 digits split by
`-`/space/`.` (`123-456` → `123456`, phone numbers excluded), 6 uppercase
alphanumerics with at least one digit and one letter (`ABC123`, not
`ABCDEF`). Bodies are fetched automatically when the provider lists
messages without bodies.

Link extraction returns the first link (trailing punctuation stripped),
optionally filtered by `domain=`, `keyword=` or a custom
`filter=callable`.

## Messages and attachments

```python
messages = mailbox.messages()                 # list (summary fields)
full = mailbox.message(messages[0].id)        # fetch full body
mailbox.mark_read(messages[0].id)             # where supported

data = mailbox.download(full, full.attachments[0])
data = mailbox.download(full, 0)              # ... or by index
```

Attachments carry either a `download_url` (mail.tm / catchmail.io) or a
zero-based `index` (mail.cx / mail.td) — `mailbox.download()` handles
both.

## HTTP behavior

Every request goes through one `httpx` client with:

- timeouts (default 30s; long-poll endpoints get larger ones),
- automatic retries for idempotent methods (GET/HEAD/OPTIONS/DELETE/PUT)
  on 408/425/429/5xx or transport errors, exponential backoff (cap 10s),
  honoring `Retry-After`; POSTs are never auto-retried (no duplicate
  accounts),
- optional `proxy=` and a default `User-Agent: multitempmail-sdk/<version>`,
- consistent error mapping (see below).

```python
client = TempMailClient(
    "mailtm",
    timeout=20,
    retries=5,
    proxy="http://127.0.0.1:8080",
)
```

## Errors

All exceptions inherit `TempMailError` and carry `status_code` and the
underlying `response`:

| Exception             | Meaning                                            |
|-----------------------|----------------------------------------------------|
| `AuthenticationError` | 401/403, or missing credentials (e.g. no mail.td key) |
| `MessageNotFound`     | 404 on a message-level operation                   |
| `MailboxExpired`      | 410 (or 404 on mailbox deletion: mail.tm/mail.td)  |
| `RateLimitError`      | 429                                                |
| `TimeoutError`        | request timeout or wait-helper expiry              |
| `WaitCancelled`       | a wait helper's `stop=` callback returned `True` (a `TimeoutError`) |
| `ProviderError`       | any other failure                                  |
| `UnsupportedFeature`  | capability not implemented by the provider         |
| `FailoverError`       | provider pool exhausted / permanent failure; carries `.failures` (name, exception) pairs |

```python
from multitempmail.errors import TempMailError, TimeoutError

try:
    otp = mailbox.wait_for_otp(timeout=60)
except TimeoutError:
    print("no code arrived")
except TempMailError as exc:
    print(f"provider error: {exc} (HTTP {exc.status_code})")
```

## Extending

Write a `BaseProvider` subclass, register it, done:

```python
from multitempmail import BaseProvider, register

@register("myprovider")
class MyProvider(BaseProvider):
    name = "myprovider"
    display_name = "My Provider"
    base_url = "https://api.myprovider.example"
    capabilities = frozenset({"create", "list", "read"})
    default_priority = 80                   # failover order
    known_domains = ("mail.myprovider.example",)

    def create(self, **kwargs):
        ...

client = TempMailClient("myprovider")
TempMailClient()                        # now includes myprovider too
```

Declared capabilities drive `supports()` / `require()`; any endpoint you
call goes through `self._request(...)` so the shared HTTP layer (timeouts,
retries, error mapping) applies automatically. Providers can override the
404→`MessageNotFound` mapping for mailbox-level operations (see
`MailTmProvider.delete_mailbox`). Registration is dynamic — a new provider
needs nothing but a `register`ed `BaseProvider` subclass. Set
`cache_messages = False` if the provider's fetch consumes mail, and wrap
repeatable lookups in `self._cache_call(key, fetcher)` to opt them into
the response cache when one is configured.

## Development

```bash
pip install -e .[dev]
python -m pytest      # 185 tests, all HTTP mocked via httpx.MockTransport
ruff check .          # lint
black --check .       # formatting
mypy multitempmail         # types
```

## Contributing

Bug reports, feature requests and pull requests are welcome. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contribution guide:
how to set up a development environment, run the test suite, linting and
type checks, and how to add a new provider.

## Support / Problems

Something not working? Check [`SUPPORT.md`](SUPPORT.md) — it covers bug
reports, provider API changes, unsupported providers, installation and
CLI issues. In short:

- **Bugs and feature requests** → open a [GitHub issue](https://github.com/0xdzik/multitempmail/issues).
- **A provider's API changed or broke** → report it so the provider
  plug-in can be updated; endpoint shapes come from provider docs only.
- **Include** Python version, package version (`pip show multitempmail`) and
  the minimal code that reproduces the problem.

## License

MIT — see [`LICENSE`](LICENSE).

## Not implemented (future work)

- Custom-domain registration/verification for mail.cx, mail.td and
  tempmail.lol v3 (the endpoints exist server-side; domain *selection* on
  `create()` is supported where documented).
- tempmail.lol v2 endpoints, scoped keys, webhooks.
- mail.cx SSE streaming endpoints.
- Async API (the SDK is structured so a parallel `AsyncProvider` layer can
  reuse the models and error hierarchy).

## Docs per provider

- mail.tm — https://docs.mail.tm
- catchmail.io — https://catchmail.io/docs
- tempmail.lol — https://api.tempmail.lol (v3)
- mail.cx — https://mail.cx/api-docs/
- mail.td — https://docs.mail.td/llms.txt
