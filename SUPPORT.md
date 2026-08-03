# Support

The fastest way to report problems or request features is the
[GitHub issue tracker](https://github.com/0xdzik/multitempmail/issues).

## Before opening an issue

- Check the [README](README.md) — especially the support matrix and the
  error table — in case the behavior you hit is documented.
- Reproduce with the latest version: `pip install --upgrade multitempmail`.

## Bug reports

Include:

- Python version and OS (`python --version`),
- package version (`pip show multitempmail`),
- the minimal code snippet that reproduces the problem,
- the full traceback / error message.

## Provider API changes

Providers are implemented against their documented APIs. If a provider
changed an endpoint, broke a response shape, or shut down:

- Say **which provider** and **which operation** failed (create, list,
  read, wait, download...),
- paste the HTTP status code and the raw response body if visible.

The plug-in will be updated against the provider's current docs.

## Unsupported providers

Have a provider that is not listed in the README? Open a feature request
with its API documentation URL. Adding a provider requires only a
`BaseProvider` subclass and tests — see `CONTRIBUTING.md`.

## Installation issues

- `pip install multitempmail` requires Python ≥ 3.10 and `httpx`.
- Import as `from multitempmail import TempMailClient`.
- If the `multitempmail` console command is missing after
  install, your `Scripts`/`bin` directory is probably not on `PATH`.

## CLI issues

- Sessions: commands read `session.json` by default; `create` writes it
  with `--save`. If a command complains about a missing session, create
  one first or pass an existing `--session` file.
- Use `multitempmail <command> --help` for exact flags.
- Verbose failures: run the same operation in Python with the SDK for a
  full traceback.

## Session / cache confusion

- Sessions persist provider names, options and the active mailbox —
  nothing else. Credentials stored are only those you passed (e.g.
  `api_key`, `api_token`, mailbox `token`/`password`).
- The response cache is **in-memory only**, enabled only when you pass
  `cache=ResponseCache(...)`, and is bypassed by every `wait_for_*`
  helper, so waits never miss new mail.

## Feature requests

Open an issue describing the use case. Good requests are specific:
what you want to do, which provider/endpoint would help, and any
documentation URL for the API involved.
