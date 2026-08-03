"""Command-line interface: ``tempmail``.

Reuses the SDK exclusively (``TempMailClient``, sessions, wait helpers);
no provider logic is duplicated here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .client import TempMailClient
from .errors import TempMailError

__all__ = ["main"]

_DEFAULT_SESSION = "session.json"


def _client_for(session: str, provider: str | None = None) -> TempMailClient:
    """Load a saved client or build a fresh one (single provider if given)."""
    try:
        return TempMailClient.load_session(session)
    except (FileNotFoundError, ValueError):
        if provider:
            return TempMailClient(provider)
        return TempMailClient()


def _ensure_mailbox(client: TempMailClient, session: str, provider: str | None = None) -> Any:
    """Return the active mailbox; create and persist one when needed."""
    mailbox = client.active
    if mailbox is None:
        if provider and client.provider.name != provider:
            client = TempMailClient(provider)
        mailbox = client.create()
        client.save_session(session)
    return mailbox


def _list_domains(provider: Any) -> list[str]:
    """Normalized domain list for display (provider-specific shapes)."""
    if provider.name == "mailcx":
        items = provider.config().get("system_domains", [])
    elif hasattr(provider, "domains"):
        items = provider.domains()
    else:
        items = []
    domains = [d["domain"] for d in items if isinstance(d, dict) and d.get("domain")]
    return domains or list(provider.known_domains)


def cmd_create(client: TempMailClient, args: argparse.Namespace) -> None:
    kwargs: dict[str, Any] = {}
    for key in ("domain", "local_part", "password", "prefix"):
        value = getattr(args, key, None)
        if value:
            kwargs[key] = value
    mailbox = client.create(**kwargs)
    client.save_session(args.save)
    print(mailbox.address)


def cmd_inbox(client: TempMailClient, args: argparse.Namespace) -> None:
    mailbox = _ensure_mailbox(client, args.session, args.provider)
    for message in mailbox.messages():
        when = message.created_at.isoformat() if message.created_at else ""
        print(f"{message.id}\t{message.sender or ''}\t{message.subject or ''}\t{when}")


def cmd_read(client: TempMailClient, args: argparse.Namespace) -> None:
    mailbox = _ensure_mailbox(client, args.session, args.provider)
    message = mailbox.message(args.message_id)
    print(f"From:    {message.sender or ''}")
    print(f"Subject: {message.subject or ''}")
    if message.body_text:
        print()
        print(message.body_text)
    elif message.body_html:
        print()
        print(message.body_html)
    if message.attachments:
        print()
        for attachment in message.attachments:
            print(f"[attachment] {attachment.filename} ({attachment.content_type or '?'})")


def cmd_otp(client: TempMailClient, args: argparse.Namespace) -> None:
    mailbox = _ensure_mailbox(client, args.session, args.provider)
    code = mailbox.wait_for_otp(timeout=args.timeout, pattern=args.pattern)
    print(code)


def cmd_wait(client: TempMailClient, args: argparse.Namespace) -> None:
    mailbox = _ensure_mailbox(client, args.session, args.provider)
    if args.otp:
        print(mailbox.wait_for_otp(timeout=args.timeout, pattern=args.pattern))
    elif args.link:
        print(mailbox.wait_for_link(keyword=args.link, timeout=args.timeout))
    else:
        message = mailbox.wait_for_message(
            timeout=args.timeout, subject=args.subject, sender=args.sender
        )
        print(f"{message.id}\t{message.sender or ''}\t{message.subject or ''}")


def cmd_delete(client: TempMailClient, args: argparse.Namespace) -> None:
    mailbox = _ensure_mailbox(client, args.session, args.provider)
    if args.mailbox:
        mailbox.delete_mailbox()
        print(f"deleted mailbox {mailbox.address}")
    elif args.message_id:
        mailbox.delete(args.message_id)
        print(f"deleted message {args.message_id}")
    else:
        raise ValueError("delete requires a MESSAGE_ID or --mailbox")


def cmd_providers(client: TempMailClient, args: argparse.Namespace) -> None:
    infos: list[dict[str, Any]] = [
        {
            "name": p.name,
            "display_name": p.display_name,
            "priority": p.priority,
            "aliases": list(p.aliases),
            "capabilities": sorted(p.capabilities),
            "known_domains": list(p.known_domains),
        }
        for p in client.providers()
    ]
    if args.json:
        print(json.dumps(infos, indent=2))
        return
    for info in infos:
        caps = ", ".join(info["capabilities"])
        print(f"{info['name']}\t{info['display_name']}\tpriority={info['priority']}\t{caps}")


def cmd_domains(client: TempMailClient, args: argparse.Namespace) -> None:
    provider = client.provider
    if args.provider:
        provider = TempMailClient(args.provider).provider
    domains = _list_domains(provider)
    if args.json:
        print(json.dumps(domains, indent=2))
        return
    for domain in domains:
        print(domain)


def cmd_health(client: TempMailClient, args: argparse.Namespace) -> None:
    snapshots = [
        {
            "name": s.name,
            "healthy": s.healthy,
            "consecutive_failures": s.consecutive_failures,
            "consecutive_successes": s.consecutive_successes,
            "total_requests": s.total_requests,
            "avg_response_time": s.avg_response_time,
            "priority": s.priority,
            "effective_priority": s.effective_priority,
            "last_error": s.last_error,
        }
        for s in client.health()
    ]
    if args.json:
        print(json.dumps(snapshots, indent=2))
        return
    for s in snapshots:
        state = "healthy" if s["healthy"] else "UNHEALTHY"
        print(
            f"{s['name']}\t{state}\tfailures={s['consecutive_failures']}\t"
            f"eff-priority={s['effective_priority']}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tempmail",
        description="Temporary email SDK — unified CLI across providers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="create a mailbox (and save a session)")
    p_create.add_argument("--provider")
    p_create.add_argument("--domain")
    p_create.add_argument("--local-part")
    p_create.add_argument("--prefix")
    p_create.add_argument("--password")
    p_create.add_argument("--timeout", type=float, help="HTTP timeout in seconds")
    p_create.add_argument("--save", default=_DEFAULT_SESSION)
    p_create.set_defaults(func=cmd_create)

    p_inbox = sub.add_parser("inbox", help="list messages")
    p_inbox.add_argument("--session", default=_DEFAULT_SESSION)
    p_inbox.add_argument("--provider")
    p_inbox.set_defaults(func=cmd_inbox)

    p_read = sub.add_parser("read", help="read a full message")
    p_read.add_argument("message_id")
    p_read.add_argument("--session", default=_DEFAULT_SESSION)
    p_read.add_argument("--provider")
    p_read.set_defaults(func=cmd_read)

    p_otp = sub.add_parser("otp", help="wait for a verification code")
    p_otp.add_argument("--timeout", type=float, default=120.0)
    p_otp.add_argument("--pattern", help="custom OTP regex")
    p_otp.add_argument("--session", default=_DEFAULT_SESSION)
    p_otp.add_argument("--provider")
    p_otp.set_defaults(func=cmd_otp)

    p_wait = sub.add_parser("wait", help="wait for a matching message")
    p_wait.add_argument("--subject")
    p_wait.add_argument("--sender")
    p_wait.add_argument("--otp", action="store_true", help="wait for a verification code")
    p_wait.add_argument("--link", help="wait for a link containing this keyword")
    p_wait.add_argument("--timeout", type=float, default=60.0)
    p_wait.add_argument("--session", default=_DEFAULT_SESSION)
    p_wait.add_argument("--provider")
    p_wait.set_defaults(func=cmd_wait)

    p_delete = sub.add_parser("delete", help="delete a message or the whole mailbox")
    p_delete.add_argument("message_id", nargs="?")
    p_delete.add_argument("--mailbox", action="store_true", help="delete the whole mailbox")
    p_delete.add_argument("--session", default=_DEFAULT_SESSION)
    p_delete.add_argument("--provider")
    p_delete.set_defaults(func=cmd_delete)

    p_providers = sub.add_parser("providers", help="list registered providers")
    p_providers.add_argument("--json", action="store_true")
    p_providers.set_defaults(func=cmd_providers)

    p_domains = sub.add_parser("domains", help="list a provider's domains")
    p_domains.add_argument("--provider")
    p_domains.add_argument("--json", action="store_true")
    p_domains.set_defaults(func=cmd_domains)

    p_health = sub.add_parser("health", help="show provider health")
    p_health.add_argument("--json", action="store_true")
    p_health.set_defaults(func=cmd_health)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        session = getattr(args, "session", None)
        if args.command == "create":
            options = {"timeout": args.timeout} if args.timeout else {}
            client = (
                TempMailClient(args.provider, **options)
                if args.provider
                else TempMailClient(**options)
            )
            cmd_create(client, args)
        elif args.command == "domains":
            client = TempMailClient()
            cmd_domains(client, args)
        else:
            client = _client_for(session, args.provider) if session else TempMailClient()
            args.func(client, args)
    except TempMailError as exc:
        print(f"tempmail: error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"tempmail: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
