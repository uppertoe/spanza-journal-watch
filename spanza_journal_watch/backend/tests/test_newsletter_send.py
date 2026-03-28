"""
Integration tests: newsletter test-send and final-send flow.

Covers:
  - send_test_newsletter: valid POST queues send_newsletter_test_email task
  - send_test_newsletter: invalid email shows error
  - send_test_newsletter: requires send_newsletters permission
  - send_test_newsletter: invalid send_token redirects to dashboard
  - send_newsletter_test_email (Celery task): sets is_test_sent=True after sending
  - send_final_newsletter: queues send_newsletter task when newsletter is ready
  - send_final_newsletter: blocks send when not ready (no test send)
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.newsletter.tasks import send_newsletter, send_newsletter_test_email
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

        with patch("spanza_journal_watch.backend.views.send_newsletter_test_email") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_test_newsletter",
                kwargs={"send_token": newsletter.send_token},
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

        with patch("spanza_journal_watch.backend.views.send_newsletter_test_email") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_test_newsletter",
                kwargs={"send_token": newsletter.send_token},
            )
            resp = client.post(url, {"email": "not-an-email"})

        assert resp.status_code == 302
        mock_task.apply_async.assert_not_called()

    def test_invalid_send_token_redirects(self):
        client, user = _make_newsletter_manager()
        url = reverse("backend:send_test_newsletter", kwargs={"send_token": "bad-token-xyz"})
        resp = client.post(url, {"email": "test@example.com"})
        assert resp.status_code == 302

    def test_requires_send_newsletters_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        newsletter = _make_newsletter()
        url = reverse(
            "backend:send_test_newsletter",
            kwargs={"send_token": newsletter.send_token},
        )
        resp = c.post(url, {"email": "test@example.com"})
        assert resp.status_code == 403

    def test_get_request_returns_400(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        url = reverse(
            "backend:send_test_newsletter",
            kwargs={"send_token": newsletter.send_token},
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

        with patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_html_content",
            return_value="<html>Test newsletter</html>",
        ), patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_txt_content",
            return_value="Test newsletter",
        ):
            send_newsletter_test_email(newsletter.pk, "recipient@example.com")

        newsletter.refresh_from_db()
        assert newsletter.is_test_sent is True

    def test_task_sends_email_to_recipient(self):
        from django.core import mail as django_mail

        newsletter = _make_newsletter()

        with patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_html_content",
            return_value="<html>Test newsletter</html>",
        ), patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_txt_content",
            return_value="Test newsletter",
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

        with patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch") as mock_batch, patch(
            "spanza_journal_watch.newsletter.tasks.send_newsletter_stats"
        ) as mock_stats:
            mock_batch.delay = MagicMock()
            mock_stats.delay = MagicMock()
            send_newsletter(newsletter.pk, test_email=False)

        newsletter.refresh_from_db()
        assert newsletter.is_sent is True
        assert newsletter.send_date is not None


# ---------------------------------------------------------------------------
# Tests: send_final_newsletter view
# ---------------------------------------------------------------------------


class TestSendFinalNewsletter:
    def test_ready_newsletter_queues_send_task(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.save()

        with patch("spanza_journal_watch.backend.views.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"send_token": newsletter.send_token},
            )
            resp = client.post(url)

        assert resp.status_code == 200
        mock_task.apply_async.assert_called_once()

    def test_not_ready_newsletter_blocks_send(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        # Not ready: no test send, ready_to_send=False
        assert not newsletter.is_ready_to_send()

        with patch("spanza_journal_watch.backend.views.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"send_token": newsletter.send_token},
            )
            resp = client.post(url)

        assert resp.status_code == 200
        mock_task.apply_async.assert_not_called()

    def test_test_sent_but_not_ready_blocks_send(self):
        client, user = _make_newsletter_manager()
        newsletter = _make_newsletter()
        newsletter.is_test_sent = True  # test sent but ready_to_send still False
        newsletter.save()

        assert not newsletter.is_ready_to_send()

        with patch("spanza_journal_watch.backend.views.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            url = reverse(
                "backend:send_final_newsletter",
                kwargs={"send_token": newsletter.send_token},
            )
            client.post(url)

        mock_task.apply_async.assert_not_called()
