#!/usr/bin/env python3
"""
Backfill EmailThread links for legacy InboundEmail rows after a database restore.

This is intended as a deploy/bootstrap helper, not a normal app migration:
it can be run repeatedly after restoring an older dump into a newer schema.

Examples:
  python deploy/bootstrap/backfill_inbox_threads.py --dry-run
  python deploy/bootstrap/backfill_inbox_threads.py --settings config.settings.production
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from email.utils import parseaddr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "spanza_journal_watch"

for path in (REPO_ROOT, APP_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill inbox EmailThread rows from legacy InboundEmail records.",
    )
    parser.add_argument(
        "--settings",
        default="config.settings.local",
        help="Django settings module (default: config.settings.local)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing to the database.",
    )
    return parser.parse_args(argv)


def setup_django(settings_module: str) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

    import django

    django.setup()


def normalize_subject(subject: str | None) -> str:
    from spanza_journal_watch.backend.signals import _normalize_subject

    return _normalize_subject(subject)


def infer_external_address(inbound_email) -> str:
    if inbound_email.sender:
        return inbound_email.sender.strip().lower()

    if inbound_email.header_sender:
        _, parsed = parseaddr(inbound_email.header_sender)
        if parsed:
            return parsed.strip().lower()

    return ""


def message_timestamp(inbound_email):
    return inbound_email.sent_timestamp or inbound_email.created


def select_thread_for_inbound(inbound_email):
    from spanza_journal_watch.backend.models import EmailThread, SentEmail

    in_reply_to = (inbound_email.in_reply_to or "").strip()
    if in_reply_to:
        sent = SentEmail.objects.filter(message_id=in_reply_to).select_related("thread").first()
        if sent and sent.thread_id:
            return sent.thread, False

    subject = normalize_subject(inbound_email.subject)
    external_address = infer_external_address(inbound_email)
    existing = (
        EmailThread.objects.filter(
            external_address=external_address,
            subject=subject,
        )
        .order_by("created", "pk")
        .first()
    )
    if existing:
        return existing, False

    thread = EmailThread(
        external_address=external_address,
        subject=subject,
        last_message_at=message_timestamp(inbound_email),
        has_unread=not inbound_email.read,
    )
    return thread, True


def recompute_thread_state(thread) -> tuple[object, bool]:
    latest_inbound = None
    unread = False

    for inbound_email in thread.inbound_messages.all().only("sent_timestamp", "created", "read"):
        at = message_timestamp(inbound_email)
        if latest_inbound is None or at > latest_inbound:
            latest_inbound = at
        if not inbound_email.read:
            unread = True

    latest_sent = None
    for sent_email in thread.sent_messages.all().only("created"):
        if latest_sent is None or sent_email.created > latest_sent:
            latest_sent = sent_email.created

    latest = latest_inbound
    if latest is None or (latest_sent is not None and latest_sent > latest):
        latest = latest_sent
    if latest is None:
        latest = thread.created

    return latest, unread


def backfill(*, dry_run: bool = False) -> dict[str, int]:
    from django.db import transaction

    from spanza_journal_watch.backend.models import EmailThread, InboundEmail

    stats = {
        "processed": 0,
        "linked": 0,
        "created_threads": 0,
        "reused_threads": 0,
        "updated_threads": 0,
    }
    touched_thread_ids: set[int] = set()

    queryset = (
        InboundEmail.objects.filter(thread__isnull=True)
        .order_by("sent_timestamp", "created", "pk")
        .iterator(chunk_size=200)
    )

    with transaction.atomic():
        for inbound_email in queryset:
            stats["processed"] += 1
            thread, created = select_thread_for_inbound(inbound_email)
            if created and not dry_run:
                thread.save()
            if created:
                stats["created_threads"] += 1
            else:
                stats["reused_threads"] += 1

            if not dry_run:
                inbound_email.thread = thread
                inbound_email.save(update_fields=["thread"])
                if thread.pk:
                    touched_thread_ids.add(thread.pk)
            stats["linked"] += 1

        if dry_run:
            transaction.set_rollback(True)
            return stats

        for thread in (
            EmailThread.objects.filter(pk__in=touched_thread_ids)
            .prefetch_related("inbound_messages", "sent_messages")
            .order_by("pk")
        ):
            last_message_at, has_unread = recompute_thread_state(thread)
            updated_fields = []
            if thread.last_message_at != last_message_at:
                thread.last_message_at = last_message_at
                updated_fields.append("last_message_at")
            if thread.has_unread != has_unread:
                thread.has_unread = has_unread
                updated_fields.append("has_unread")
            if updated_fields:
                thread.save(update_fields=updated_fields)
                stats["updated_threads"] += 1

    return stats


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    setup_django(args.settings)
    stats = backfill(dry_run=args.dry_run)

    mode = "dry-run" if args.dry_run else "write"
    print(f"Backfill mode: {mode}")
    print(f"Inbound emails processed: {stats['processed']}")
    print(f"Inbound emails linked: {stats['linked']}")
    print(f"Threads created: {stats['created_threads']}")
    print(f"Threads reused: {stats['reused_threads']}")
    print(f"Threads recomputed: {stats['updated_threads']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
