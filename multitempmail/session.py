"""Session persistence: save/restore a client (and its active mailbox).

Sessions are JSON files that keep exactly the information needed to restore
a mailbox: provider names, client options (credentials such as ``api_key`` /
``api_token`` are stored as given — pass ``jsonable`` values only) and the
active mailbox's address, token and password.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Mailbox
from .utils import parse_dt

__all__ = [
    "SESSION_VERSION",
    "load_session",
    "mailbox_from_dict",
    "mailbox_to_dict",
    "save_session",
]

SESSION_VERSION = 1

_JSONABLE = (str, int, float, bool, type(None))


def clean_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep only values that survive a JSON round-trip (drops ``http``,
    custom transports, callables...)."""
    cleaned: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, _JSONABLE + (list, dict)):
            cleaned[key] = value
    return cleaned


def mailbox_to_dict(mailbox: Mailbox | None) -> dict[str, Any] | None:
    """Serialize a mailbox (or ``None``)."""
    if mailbox is None:
        return None
    return {
        "provider": mailbox.provider,
        "address": mailbox.address,
        "token": mailbox.token,
        "password": mailbox.password,
        "expires_at": mailbox.expires_at.isoformat() if mailbox.expires_at else None,
        "created_at": mailbox.created_at.isoformat() if mailbox.created_at else None,
        "raw": mailbox.raw,
    }


def mailbox_from_dict(data: dict[str, Any]) -> Mailbox:
    """Deserialize a mailbox (unbound; see ``TempMailClient.load_session``
    to restore a bound mailbox)."""
    return Mailbox(
        provider=str(data["provider"]),
        address=str(data["address"]),
        token=data.get("token"),
        password=data.get("password"),
        expires_at=parse_dt(data.get("expires_at")),
        created_at=parse_dt(data.get("created_at")),
        raw=data.get("raw") or {},
    )


def save_session(
    path: str | Path,
    *,
    providers: list[str],
    options: dict[str, Any],
    mailbox: Mailbox | None = None,
) -> None:
    """Write a session file containing the provider pool, the client
    options and the active mailbox (if any)."""
    payload = {
        "version": SESSION_VERSION,
        "providers": list(providers),
        "options": clean_options(options),
        "mailbox": mailbox_to_dict(mailbox),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))


def load_session(path: str | Path) -> dict[str, Any]:
    """Read and validate a session file, returning its payload dict."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"session file not found: {target}")
    payload = json.loads(target.read_text())
    if payload.get("version") != SESSION_VERSION:
        raise ValueError(f"unsupported session version: {payload.get('version')!r}")
    if not payload.get("providers"):
        raise ValueError("session contains no providers")
    return payload
