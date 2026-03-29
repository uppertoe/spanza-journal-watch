import pytest
from django.utils import timezone

from deploy.bootstrap.backfill_inbox_threads import backfill
from spanza_journal_watch.backend.models import EmailThread, InboundEmail, SentEmail
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def make_inbound(**kwargs):
    defaults = {
        "sender": "sender@example.com",
        "subject": "Hello",
        "sent_timestamp": timezone.now(),
        "read": False,
    }
    defaults.update(kwargs)
    return InboundEmail.objects.create(**defaults)


def test_backfill_creates_thread_and_links_unthreaded_inbound():
    inbound = make_inbound()

    stats = backfill()

    inbound.refresh_from_db()
    assert stats["processed"] == 1
    assert stats["created_threads"] == 1
    assert inbound.thread is not None
    assert inbound.thread.external_address == "sender@example.com"
    assert inbound.thread.subject == "Hello"
    assert inbound.thread.has_unread is True


def test_backfill_is_idempotent_on_rerun():
    inbound = make_inbound()

    first = backfill()
    second = backfill()

    inbound.refresh_from_db()
    assert first["linked"] == 1
    assert second["processed"] == 0
    assert second["linked"] == 0
    assert EmailThread.objects.count() == 1
    assert inbound.thread_id == EmailThread.objects.get().pk


def test_backfill_reuses_thread_for_same_sender_and_normalized_subject():
    first = make_inbound(subject="Hello")
    second = make_inbound(subject="Re: Hello", sent_timestamp=timezone.now() + timezone.timedelta(minutes=1))

    stats = backfill()

    first.refresh_from_db()
    second.refresh_from_db()
    assert stats["created_threads"] == 1
    assert first.thread_id == second.thread_id
    assert EmailThread.objects.count() == 1
    assert EmailThread.objects.get().subject == "Hello"


def test_backfill_prefers_in_reply_to_sent_message_thread():
    user = UserFactory()
    thread = EmailThread.objects.create(
        external_address="person@example.com",
        subject="Question",
        last_message_at=timezone.now() - timezone.timedelta(days=1),
        has_unread=False,
    )
    SentEmail.objects.create(
        thread=thread,
        recipient="person@example.com",
        subject="Re: Question",
        body="Reply",
        message_id="<reply-1@example.com>",
        sent_by=user,
    )
    inbound = make_inbound(
        sender="person@example.com",
        subject="Re: Question",
        in_reply_to="<reply-1@example.com>",
        sent_timestamp=timezone.now(),
    )

    stats = backfill()

    inbound.refresh_from_db()
    thread.refresh_from_db()
    assert stats["reused_threads"] == 1
    assert inbound.thread_id == thread.pk
    assert thread.has_unread is True


def test_backfill_dry_run_leaves_database_unchanged():
    inbound = make_inbound()

    stats = backfill(dry_run=True)

    inbound.refresh_from_db()
    assert stats["processed"] == 1
    assert stats["linked"] == 1
    assert inbound.thread_id is None
    assert EmailThread.objects.count() == 0
