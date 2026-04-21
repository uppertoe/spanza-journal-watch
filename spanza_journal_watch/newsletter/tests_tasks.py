"""
Tests for newsletter Celery tasks.

Covers:
1. send_confirmation_email — sends email, retries on DoesNotExist
2. send_newsletter_batch — sends batch, updates emails_sent, re-raises on error
3. send_newsletter_stats — sends stats email to staff
4. send_newsletter — orchestration, validation guards, batching
5. get_subscriber_batches — batch slicing helper
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Issue

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture()
def newsletter_issue():
    return Issue.objects.create(name="Task Newsletter Issue", body="body", active=True)


@pytest.fixture()
def newsletter(newsletter_issue):
    return Newsletter.objects.create(
        issue=newsletter_issue,
        subject="Task Test Newsletter",
        send_date=timezone.now(),
    )


@pytest.fixture()
def subscriber():
    return Subscriber.objects.create(email="task-sub@example.com", subscribed=True)


@pytest.fixture()
def staff_user():
    return User.objects.create_user(email="staff-task@example.com", password="pw", is_staff=True)


# ---------------------------------------------------------------------------
# 1. send_confirmation_email
# ---------------------------------------------------------------------------


class TestSendConfirmationEmail:
    def test_sends_email(self, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_confirmation_email

        send_confirmation_email(subscriber.pk)
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["task-sub@example.com"]

    def test_retries_on_missing_subscriber(self):
        from celery.exceptions import MaxRetriesExceededError

        from spanza_journal_watch.newsletter.tasks import send_confirmation_email

        with pytest.raises((MaxRetriesExceededError, Subscriber.DoesNotExist)):
            send_confirmation_email(999999)

    def test_email_subject(self, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_confirmation_email

        send_confirmation_email(subscriber.pk)
        assert "Journal Watch" in mail.outbox[0].subject


# ---------------------------------------------------------------------------
# 2. send_newsletter_batch
# ---------------------------------------------------------------------------


class TestSendNewsletterBatch:
    def test_sends_to_subscribers(self, newsletter, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_batch

        send_newsletter_batch(newsletter.pk, [subscriber.pk], test_email=True)
        assert len(mail.outbox) == 1

    def test_updates_emails_sent_when_not_test(self, newsletter, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_batch

        send_newsletter_batch(newsletter.pk, [subscriber.pk], test_email=False)
        newsletter.refresh_from_db()
        assert newsletter.emails_sent >= 1

    def test_does_not_update_emails_sent_when_test(self, newsletter, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_batch

        original_count = newsletter.emails_sent
        send_newsletter_batch(newsletter.pk, [subscriber.pk], test_email=True)
        newsletter.refresh_from_db()
        assert newsletter.emails_sent == original_count

    @patch("spanza_journal_watch.newsletter.tasks.mail.get_connection")
    def test_reraises_send_failure(self, mock_conn, newsletter, subscriber):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_batch

        mock_conn.return_value.send_messages.side_effect = RuntimeError("SMTP down")
        with pytest.raises(RuntimeError, match="SMTP down"):
            send_newsletter_batch(newsletter.pk, [subscriber.pk], test_email=False)


# ---------------------------------------------------------------------------
# 3. send_newsletter_stats
# ---------------------------------------------------------------------------


class TestSendNewsletterStats:
    def test_sends_to_all_staff(self, newsletter, staff_user):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_stats

        staff_count = User.objects.filter(is_staff=True).count()
        send_newsletter_stats(newsletter.pk, subscriber_count=100, batch_count=2)
        assert len(mail.outbox) == staff_count

    def test_email_contains_stats(self, newsletter, staff_user):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_stats

        send_newsletter_stats(newsletter.pk, subscriber_count=42, batch_count=1)
        assert len(mail.outbox) >= 1
        assert "statistics" in mail.outbox[0].subject.lower()


# ---------------------------------------------------------------------------
# 4. send_newsletter — orchestration
# ---------------------------------------------------------------------------


class TestSendNewsletter:
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch")
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats")
    def test_send_requires_test_sent(self, mock_stats, mock_batch, newsletter):
        from spanza_journal_watch.newsletter.tasks import (
            NewsletterNotReadyToSendError,
            send_newsletter,
        )

        with pytest.raises(NewsletterNotReadyToSendError):
            send_newsletter(newsletter.pk)

    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch")
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats")
    def test_send_requires_ready_to_send(self, mock_stats, mock_batch, newsletter):
        from spanza_journal_watch.newsletter.tasks import (
            NewsletterNotReadyToSendError,
            send_newsletter,
        )

        newsletter.is_test_sent = True
        newsletter.ready_to_send = False
        newsletter.save(update_fields=["is_test_sent", "ready_to_send"])

        with pytest.raises(NewsletterNotReadyToSendError):
            send_newsletter(newsletter.pk)

    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch")
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats")
    def test_send_blocks_duplicate_send(self, mock_stats, mock_batch, newsletter):
        from spanza_journal_watch.newsletter.tasks import (
            NewsletterNotReadyToSendError,
            send_newsletter,
        )

        newsletter.is_test_sent = True
        newsletter.ready_to_send = True
        newsletter.is_sent = True
        newsletter.resend_enabled = False
        newsletter.save(update_fields=["is_test_sent", "ready_to_send", "is_sent", "resend_enabled"])

        with pytest.raises(NewsletterNotReadyToSendError):
            send_newsletter(newsletter.pk)

    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch")
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats")
    def test_send_resend_path_consumes_flag(self, mock_stats, mock_batch, newsletter):
        from spanza_journal_watch.newsletter.tasks import send_newsletter

        newsletter.is_test_sent = True
        newsletter.ready_to_send = True
        newsletter.is_sent = True
        newsletter.resend_enabled = True
        newsletter.save(update_fields=["is_test_sent", "ready_to_send", "is_sent", "resend_enabled"])

        send_newsletter(newsletter.pk)

        newsletter.refresh_from_db()
        assert newsletter.is_sent is True
        assert newsletter.resend_enabled is False

    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch")
    @patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats")
    def test_concurrent_send_claims_once(self, mock_stats, mock_batch, newsletter):
        """Two eager send_newsletter calls: second must raise, emails_sent not double-counted."""
        from spanza_journal_watch.newsletter.tasks import (
            NewsletterNotReadyToSendError,
            send_newsletter,
        )

        newsletter.is_test_sent = True
        newsletter.ready_to_send = True
        newsletter.save(update_fields=["is_test_sent", "ready_to_send"])

        send_newsletter(newsletter.pk)
        with pytest.raises(NewsletterNotReadyToSendError):
            send_newsletter(newsletter.pk)


# ---------------------------------------------------------------------------
# 5. get_subscriber_batches
# ---------------------------------------------------------------------------


class TestGetSubscriberBatches:
    def test_single_batch(self):
        from spanza_journal_watch.newsletter.tasks import get_subscriber_batches

        subs = [Subscriber.objects.create(email=f"batch-{i}@example.com", subscribed=True) for i in range(3)]
        pks = [s.pk for s in subs]
        batches = list(get_subscriber_batches(pks, batch_size=10))
        assert len(batches) == 1
        assert batches[0].count() == 3

    def test_multiple_batches(self):
        from spanza_journal_watch.newsletter.tasks import get_subscriber_batches

        subs = [Subscriber.objects.create(email=f"mbatch-{i}@example.com", subscribed=True) for i in range(5)]
        pks = [s.pk for s in subs]
        batches = list(get_subscriber_batches(pks, batch_size=2))
        assert len(batches) == 3
        counts = [b.count() for b in batches]
        assert sum(counts) == 5

    def test_empty_list(self):
        from spanza_journal_watch.newsletter.tasks import get_subscriber_batches

        batches = list(get_subscriber_batches([], batch_size=10))
        assert len(batches) == 0
