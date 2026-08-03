# Contributing

Thanks for considering a contribution to `tempmail-api`. This guide
covers the local setup, the verification workflow and how to add a new
provider.

## Local setup

```bash
git clone https://github.com/0xdzik/tempmail-api.git
cd tempmail-api
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

The package requires Python ≥ 3.10.

## Verification

Run these before submitting anything — all four must pass:

```bash
python -m pytest      # or: pytest
ruff check .
black --check .
mypy tempmail
```

- `pytest` runs the full suite (all HTTP calls are mocked via
  `httpx.MockTransport` — no real network).
- `ruff` lints; `black` enforces formatting (line-length 100);
  `mypy` type-checks the `tempmail/` package strictly.

Fix what the tools report, then re-run. If a check fails for code you did
not touch, say so in the PR description rather than weakening the config.

## How to add a new provider

1. Create `tempmail/providers/<name>.py` with a class inheriting from
   `BaseProvider` (see `mailcx.py` for a compact example).
2. Declare metadata: `name`, `display_name`, `base_url`, `capabilities`
   (a `frozenset` of `Cap` values), `default_priority`, `known_domains`.
3. Implement **documented endpoints only** — read the provider's API docs.
   Never invent or guess undocumented behavior; ambiguous response shapes
   must be parsed defensively.
4. Register the provider: `from tempmail import register; @register(...)`
   and import the module from `tempmail/providers/__init__.py`.
5. Add tests in `tests/test_<name>.py` using the mocked-HTTP helpers from
   `tests/conftest.py` (`Router`, `json_response`, `make_http`).
6. Update the README provider list, the support matrix and the per-provider
   docs section.

See `AGENTS.md` for the architecture rules (shared HTTP layer, no
duplicated provider logic, capability enforcement, session/cache
integration).

## Updating documentation

- README changes must stay consistent with the support matrix and the
  install instructions (`pip install tempmail-api`, import
  `from tempmail import TempMailClient`).
- If you change public behavior, update the README sections that describe
  it (wait helpers, cache, sessions, CLI, errors).

## Submitting a pull request

1. Create a branch: `git checkout -b fix/descriptive-name`.
2. Make your changes, then run the verification commands above.
3. Commit with a concise message describing the change.
4. Push and open a pull request against `main`; reference any related
   issue.

### PR checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] `black --check .` passes
- [ ] `mypy tempmail` passes
- [ ] No undocumented API behavior invented
- [ ] Public API kept backward compatible (or a deprecation path added)
