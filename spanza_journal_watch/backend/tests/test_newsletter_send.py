"""
Integration tests: newsletter test-send and final-send flow.

Covers:
  - send_test_newsletter: valid POST queues send_newsletter_test_email task
  - send_test_newsletter: invalid email shows error
  - send_test_newsletter: requires send_newsletters permission
  - send_test_newsletter: unknown pk redirects to dashboard
  - send_newsletter_test_email (Celery task): sets is_test_sent=True after sending
  - send_final_newsletter: queues send_newsletter task when newsletter is ready
  - send_final_newsletter: blocks send when not ready (no test send)
"""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.newsletter.tasks import (
    send_newsletter,
    send_newsletter_test_email,
    send_scheduled_newsletters,
)
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEND_NEWSLETTERS = "backend.send_newsletters"
MANAGE_ISSUE_BUILDER = "submissions.manage_issue_builder"
CHIEF_EDITOR = "submissions.chief_editor"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def _make_newsletter_manager():
    u = UserFactory()
    _grant(u, SEND_NEWSLETTERS, CHIEF_EDITOR, MANAGE_ISSUE_BUILDER)
    c = Client()
    c.force_login(u)
    return c, u


def _make_newsletter(name="Jan 2024 Newsletter"):
    issue = Issue.objects.create(name=name, body="Issue body")
    newsletter = Newsletter.objects.create(
        issue=issue,
        subject=f"SPANZA {name}",
    )
    return newsletter


# ---------------------------------------------------------------------------
# Tests: send_test_newsletter view
# ---------------------------------------------------------------------------


class TestSendTestNewsletter:
    def test_valid_post_queues_task_and_redirects(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()

        with patch("spanza_journal_watch.backend.views.newsletter_release.send_newsletter_test_email") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_test_newsletter",
                kwargs={"pk": newsletter.pk},
            )
            resp = client.post(url, {"email": "test@example.com"})

        # Should redirect to final_newsletter page
        assert resp.status_code == 302
        mock_task.apply_async.assert_called_once()
        call_args = mock_task.apply_async.call_args
        assert call_args[0][0] == (newsletter.pk, "test@example.com")

    def test_invalid_email_does_not_queue_task(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()

        with patch("spanza_journal_watch.backend.views.newsletter_release.send_newsletter_test_email") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_test_newsletter",
                kwargs={"pk": newsletter.pk},
            )
            resp = client.post(url, {"email": "not-an-email"})

        assert resp.status_code == 302
        mock_task.apply_async.assert_not_called()

    def test_unknown_pk_redirects(self):
        client, user = _make_newsletter_manager()
        url = reverse("backend:send_test_newsletter", kwargs={"pk": 999999})
        resp = client.post(url, {"email": "test@example.com"})
        assert resp.status_code == 302

    def test_requires_send_newsletters_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        newsletter = _make_newsletter()
        url = reverse(
            "backend:send_test_newsletter",
            kwargs={"pk": newsletter.pk},
        )
        resp = c.post(url, {"email": "test@example.com"})
        assert resp.status_code == 403

    def test_get_request_returns_400(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        url = reverse(
            "backend:send_test_newsletter",
            kwargs={"pk": newsletter.pk},
        )
        resp = client.get(url)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: send_newsletter_test_email Celery task
# ---------------------------------------------------------------------------


class TestSendNewsletterTestEmailTask:
    def test_task_sets_is_test_sent_true(self):
        newsletter = _make_newsletter()
        Subscriber.objects.create(email="sub@example.com", subscribed=True)

        assert newsletter.is_test_sent is False

        with (
            patch(
                "spanza_journal_watch.newsletter.models.Newsletter.generate_html_content",
                return_value="<html>Test newsletter</html>",
            ),
            patch(
                "spanza_journal_watch.newsletter.models.Newsletter.generate_txt_content",
                return_value="Test newsletter",
            ),
        ):
            send_newsletter_test_email(newsletter.pk, "recipient@example.com")

        newsletter.refresh_from_db()
        assert newsletter.is_test_sent is True

    def test_task_sends_email_to_recipient(self):
        from django.core import mail as django_mail

        newsletter = _make_newsletter()

        with (
            patch(
                "spanza_journal_watch.newsletter.models.Newsletter.generate_html_content",
                return_value="<html>Test newsletter</html>",
            ),
            patch(
                "spanza_journal_watch.newsletter.models.Newsletter.generate_txt_content",
                return_value="Test newsletter",
            ),
        ):
            send_newsletter_test_email(newsletter.pk, "test.recipient@example.com")

        assert len(django_mail.outbox) >= 1


class TestSendNewsletterTask:
    def test_final_send_sets_send_date(self):
        newsletter = _make_newsletter()
        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.save()
        Subscriber.objects.create(email="sub@example.com", subscribed=True)

        with (
            patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch") as mock_batch,
            patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats") as mock_stats,
            patch("spanza_journal_watch.newsletter.tasks.chord"),
        ):
            mock_batch.si = MagicMock()
            mock_stats.si = MagicMock()
            send_newsletter(newsletter.pk)

        newsletter.refresh_from_db()
        assert newsletter.is_sent is True
        assert newsletter.send_date is not None


# ---------------------------------------------------------------------------
# Tests: send_final_newsletter view
# ---------------------------------------------------------------------------


class TestSendFinalNewsletter:
    def test_get_request_returns_400(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()

        url = reverse(
            "backend:send_final_newsletter",
            kwargs={"pk": newsletter.pk},
        )
        resp = client.get(url)

        assert resp.status_code == 400

    def test_ready_newsletter_queues_send_task(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.save()

        with patch("spanza_journal_watch.backend.views.newsletter_release.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"pk": newsletter.pk},
            )
            resp = client.post(url)

        assert resp.status_code == 302
        assert resp.url == reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk})
        mock_task.apply_async.assert_called_once()

    def test_not_ready_newsletter_blocks_send(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        # Not ready: no test send, ready_to_send=False
        assert not newsletter.is_ready_to_send()

        with patch("spanza_journal_watch.backend.views.newsletter_release.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"pk": newsletter.pk},
            )
            resp = client.post(url)

        assert resp.status_code == 302
        mock_task.apply_async.assert_not_called()

    def test_test_sent_but_not_ready_blocks_send(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        newsletter.is_test_sent = True  # test sent but ready_to_send still False
        newsletter.save()

        assert not newsletter.is_ready_to_send()

        with patch("spanza_journal_watch.backend.views.newsletter_release.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"pk": newsletter.pk},
            )
            client.post(url)

        mock_task.apply_async.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: scheduled sends
# ---------------------------------------------------------------------------


def _make_ready_newsletter():
    newsletter = _make_newsletter()
    newsletter.ready_to_send = True
    newsletter.is_test_sent = True
    newsletter.save()
    return newsletter


def _local_input(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


class TestScheduleNewsletter:
    def test_future_time_is_saved_in_melbourne_time(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_ready_newsletter()

        resp = client.post(
            reverse("backend:schedule_newsletter", kwargs={"pk": newsletter.pk}),
            {"send_at": "2099-01-15T09:00"},
        )

        assert resp.status_code == 302
        newsletter.refresh_from_db()
        local = timezone.localtime(newsletter.scheduled_send_at)
        assert (local.year, local.hour, local.minute, str(local.tzinfo)) == (2099, 9, 0, "Australia/Melbourne")
        assert newsletter.scheduled_by_email == user.email

    def test_past_time_is_refused(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_ready_newsletter()

        client.post(
            reverse("backend:schedule_newsletter", kwargs={"pk": newsletter.pk}),
            {"send_at": _local_input(timezone.now() - datetime.timedelta(hours=1))},
        )

        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is None

    def test_untested_newsletter_is_refused(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_newsletter()

        client.post(
            reverse("backend:schedule_newsletter", kwargs={"pk": newsletter.pk}),
            {"send_at": "2099-01-15T09:00"},
        )

        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is None

    def test_page_shows_schedule_form_then_scheduled_time(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_ready_newsletter()
        url = reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk})

        assert "Schedule send" in client.get(url).content.decode()

        newsletter.scheduled_send_at = timezone.make_aware(datetime.datetime(2099, 1, 15, 9, 0))
        newsletter.save()
        content = client.get(url).content.decode()
        assert "scheduled to be sent to all subscribers at 9:00 am on Thursday 15 January 2099" in content
        assert "Cancel scheduled send" in content

    def test_cancel_clears_schedule(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_ready_newsletter()
        newsletter.scheduled_send_at = timezone.now() + datetime.timedelta(days=1)
        newsletter.save()

        client.post(reverse("backend:cancel_scheduled_newsletter", kwargs={"pk": newsletter.pk}))

        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is None

    def test_editing_content_clears_schedule(self):
        newsletter = _make_ready_newsletter()
        newsletter.scheduled_send_at = timezone.now() + datetime.timedelta(days=1)
        newsletter.save()

        newsletter.subject = "Changed subject"
        newsletter.save()

        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is None
        assert not newsletter.is_test_sent


class TestSendScheduledNewsletters:
    def test_due_newsletter_is_sent_once(self):
        newsletter = _make_ready_newsletter()
        newsletter.scheduled_send_at = timezone.now() - datetime.timedelta(minutes=1)
        newsletter.scheduled_by_email = "editor@example.com"
        newsletter.save()

        with patch("spanza_journal_watch.newsletter.tasks.send_newsletter") as mock_send:
            send_scheduled_newsletters()
            send_scheduled_newsletters()

        mock_send.delay.assert_called_once_with(newsletter.pk, sender_email="editor@example.com")
        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is None

    def test_future_newsletter_is_left_alone(self):
        newsletter = _make_ready_newsletter()
        newsletter.scheduled_send_at = timezone.now() + datetime.timedelta(hours=1)
        newsletter.save()

        with patch("spanza_journal_watch.newsletter.tasks.send_newsletter") as mock_send:
            send_scheduled_newsletters()

        mock_send.delay.assert_not_called()
        newsletter.refresh_from_db()
        assert newsletter.scheduled_send_at is not None

    def test_unready_newsletter_is_not_sent(self):
        newsletter = _make_newsletter()
        Newsletter.objects.filter(pk=newsletter.pk).update(
            scheduled_send_at=timezone.now() - datetime.timedelta(minutes=1)
        )

        with patch("spanza_journal_watch.newsletter.tasks.send_newsletter") as mock_send:
            send_scheduled_newsletters()

        mock_send.delay.assert_not_called()


class TestTestSendPolling:
    def test_page_polls_while_queued_test_is_pending(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_newsletter()
        url = reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk})

        content = client.get(url + "?test_queued=1").content.decode()

        assert 'hx-trigger="every 2s"' in content
        assert "The test email is being sent." in content
        assert 'hx-trigger="every 2s"' not in client.get(url).content.decode()

    def test_poll_returns_send_controls_once_tested(self):
        client, _ = _make_newsletter_manager()
        newsletter = _make_ready_newsletter()
        url = reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk})

        content = client.get(url + "?test_queued=1", headers={"HX-Request": "true"}).content.decode()

        assert 'hx-trigger="every 2s"' not in content
        assert "Send Newsletter" in content
        assert 'id="newsletter-status" class="mb-3" hx-swap-oob="true"' in content
        assert "<h1>" not in content
