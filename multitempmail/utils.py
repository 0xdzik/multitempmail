"""Shared helpers: OTP/link extraction, address generation, datetime parsing."""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_OTP_PATTERNS",
    "extract_link",
    "extract_links",
    "extract_otp",
    "extract_otps",
    "is_valid_domain",
    "is_valid_local_part",
    "parse_dt",
    "random_local_part",
    "random_password",
    "substring_match",
    "validate_domain",
    "validate_local_part",
]

#: six consecutive digits, e.g. ``123456``
OTP_PATTERN_SIX_DIGITS = r"\b\d{6}\b"
#: six digits split by a separator, e.g. ``123-456`` or ``123 456``. The
#: look-around guards reject digits that are part of a longer number chain
#: (e.g. ``123-456-789`` or a phone number like ``555-123-4567``).
OTP_PATTERN_SPLIT = r"(?<![\d][\s.-])(\d{3})[\s.-](\d{3})\b(?![\s.-]\d)"
#: six uppercase letters/digits mixing both kinds, e.g. ``ABC123`` (purely
#: numeric and purely alphabetic tokens are not treated as codes)
OTP_PATTERN_ALNUM = r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6}\b"

#: patterns tried in order by :func:`extract_otp` (separators are stripped)
DEFAULT_OTP_PATTERNS = (OTP_PATTERN_SIX_DIGITS, OTP_PATTERN_SPLIT, OTP_PATTERN_ALNUM)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

_ALNUM = string.ascii_lowercase + string.digits


def extract_otps(text: str | None, pattern: str | None = None) -> list[str]:
    """Return all OTP-like codes found in ``text``, in order, deduplicated.

    With ``pattern=None`` the :data:`DEFAULT_OTP_PATTERNS` are tried in
    order (123456, then 123-456/123 456, then ABC123); split codes are
    normalized to six digits. A custom ``pattern`` overrides the defaults
    entirely.
    """
    if not text:
        return []
    if pattern:
        return list(dict.fromkeys(re.findall(pattern, text)))
    found: list[str] = []
    for current in DEFAULT_OTP_PATTERNS:
        for match in re.findall(current, text):
            code = "".join(match) if isinstance(match, tuple) else match
            if code not in found:
                found.append(code)
    return found


def extract_otp(text: str | None, pattern: str | None = None) -> str | None:
    """Return the first OTP-like code in ``text``, or ``None``.

    See :func:`extract_otps` for the matching rules.
    """
    codes = extract_otps(text, pattern)
    return codes[0] if codes else None


def extract_links(text: str | None) -> list[str]:
    """Return all ``http(s)://`` URLs found in ``text``, deduplicated.

    Trailing punctuation (``.``, ``,``, ``;``, ``:``, ``!``, ``?``, ``)``,
    ``]``, ``}``) is stripped from the end of each URL.
    """
    if not text:
        return []
    cleaned = [re.sub(r"[.,;:!?)\]}]+$", "", m) for m in _URL_RE.findall(text)]
    return list(dict.fromkeys(cleaned))


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower()
    host = host.removeprefix("www.")
    domain = domain.lower()
    domain = domain.removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def extract_link(
    text: str | None,
    *,
    keyword: str | None = None,
    domain: str | None = None,
    filter: Callable[[str], bool] | None = None,
) -> str | None:
    """Return the first URL in ``text`` matching the optional filters.

    Args:
        text: text to scan.
        keyword: only URLs containing this substring (case-insensitive)
            are considered.
        domain: only URLs whose host is ``domain`` (or a subdomain of it)
            are considered.
        filter: optional callback returning True for URLs to keep.
    """
    for link in extract_links(text):
        if keyword is not None and keyword.lower() not in link.lower():
            continue
        if domain is not None:
            host = urlsplit(link).hostname
            if not host or not _host_matches(host, domain):
                continue
        if filter is not None and not filter(link):
            continue
        return link
    return None


def random_local_part(
    prefix: str | None = None,
    length: int = 8,
    pattern: str | None = None,
    max_length: int | None = None,
) -> str:
    """Generate a random lowercase local part for an email address.

    ``prefix`` is kept verbatim, ``length`` random alphanumeric characters
    are appended. When ``pattern`` is given the result is validated with
    ``re.fullmatch`` (re-generated up to 20 times) and ``max_length`` caps
    the total length.
    """
    prefix = prefix or ""
    budget = max_length if max_length is not None else length + len(prefix)
    n = max(1, min(length, budget - len(prefix)))
    rx = re.compile(pattern) if pattern else None
    for _ in range(20):
        part = prefix + "".join(secrets.choice(_ALNUM) for _ in range(n))
        if rx is None or rx.fullmatch(part):
            return part
    return prefix + "".join(secrets.choice(_ALNUM) for _ in range(n))


#: a DNS-style domain: at least one dot, RFC-1123 labels, 253 chars max
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)" r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+" r"[a-z]{2,63}$"
)

#: the local part of an address: 1-64 chars of alnum plus . _ - %
_LOCAL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?$")

LOCAL_PART_MAX = 64
DOMAIN_MAX = 253


def is_valid_domain(domain: object) -> bool:
    """True when ``domain`` is a syntactically valid DNS domain name."""
    if not isinstance(domain, str):
        return False
    value = domain.strip().lower()
    if not value or len(value) > DOMAIN_MAX:
        return False
    # The regex already rejects a label that starts/ends with a hyphen and any
    # empty label, so a full match is the whole check.
    return bool(_DOMAIN_RE.fullmatch(value))


def is_valid_local_part(local: object) -> bool:
    """True when ``local`` is a usable email local part.

    Rejects an embedded ``@`` (which would yield a two-``@`` address), spaces,
    control characters, leading/trailing dots or separators, and anything over
    :data:`LOCAL_PART_MAX` characters.
    """
    if not isinstance(local, str):
        return False
    if not (1 <= len(local) <= LOCAL_PART_MAX):
        return False
    if not _LOCAL_RE.fullmatch(local):
        return False
    return ".." not in local


def validate_domain(domain: object, allowed: tuple[str, ...] | list[str] | None = None) -> str:
    """Return ``domain`` normalised, or raise ``ValueError`` explaining why not.

    When ``allowed`` is given, the domain must appear in it (case-insensitive);
    the error names every accepted value so the caller can recover.
    """
    if not is_valid_domain(domain):
        raise ValueError(f"invalid domain {domain!r}: expected a DNS name such as 'example.com'")
    value = str(domain).strip().lower()
    if allowed:
        permitted = tuple(a.strip().lower() for a in allowed)
        if value not in permitted:
            raise ValueError(
                f"unsupported domain {domain!r}: allowed domains are {', '.join(permitted)}"
            )
    return value


def validate_local_part(local: object) -> str:
    """Return ``local`` unchanged, or raise ``ValueError`` explaining why not."""
    if not is_valid_local_part(local):
        raise ValueError(
            f"invalid local part {local!r}: use 1-{LOCAL_PART_MAX} characters of "
            "letters, digits, '.', '_', '%', '+', '-' with no '@', spaces, or "
            "leading/trailing separators"
        )
    return str(local)


def random_password(length: int = 16) -> str:
    """Generate a random ASCII password of the given length."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def parse_dt(value: object) -> datetime | None:
    """Parse an ISO-8601 string, or a unix timestamp (seconds or
    milliseconds, int or float), into a timezone-aware UTC datetime.
    Returns ``None`` for anything unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                # naive fallback; normalized to UTC below (noqa: DTZ007)
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def substring_match(text: str | None, query: str | None) -> bool:
    """Case-insensitive substring match; ``None`` values are skipped."""
    if query is None:
        return True
    if text is None:
        return False
    return query.lower() in text.lower()
