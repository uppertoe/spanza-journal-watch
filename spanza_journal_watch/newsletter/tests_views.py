"""
Tests for newsletter subscribe and toggle views.

Covers:
1. subscribe — requires HTMX header, creates subscriber, re-subscribes existing
2. toggle_subscription — requires login, creates if missing, toggles if existing
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from spanza_journal_watch.newsletter.models import Subscriber

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. subscribe
# ---------------------------------------------------------------------------


class TestSubscribeView:
    def test_non_htmx_returns_400(self, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "test@example.com"},
        )
        assert response.status_code == 400

    def test_htmx_get_returns_form(self, client):
        response = client.get(
            reverse("newsletter:subscribe"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_htmx_post_creates_subscriber(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "newsub@example.com"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 302
        assert Subscriber.objects.filter(email="newsub@example.com").exists()
        mock_task.delay.assert_called_once()

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_htmx_post_resubscribes_existing(self, mock_task, client):
        sub = Subscriber.objects.create(email="existing@example.com", subscribed=False)
        client.post(
            reverse("newsletter:subscribe"),
            {"email": "existing@example.com"},
            HTTP_HX_REQUEST="true",
        )
        sub.refresh_from_db()
        assert sub.subscribed is True

    def test_htmx_post_sets_session(self, client):
        with patch("spanza_journal_watch.newsletter.views.send_confirmation_email"):
            client.post(
                reverse("newsletter:subscribe"),
                {"email": "session@example.com"},
                HTTP_HX_REQUEST="true",
            )
        assert client.session.get("subscribed") is True
        assert client.session.get("subscriber_id") is not None


# ---------------------------------------------------------------------------
# 2. toggle_subscription
# ---------------------------------------------------------------------------


class TestToggleSubscription:
    def test_requires_login(self, client):
        response = client.post(reverse("newsletter:toggle_subscription"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_requires_post(self, client):
        user = User.objects.create_user(email="toggle-get@example.com", password="pw")
        client.force_login(user)
        response = client.get(reverse("newsletter:toggle_subscription"))
        assert response.status_code == 405

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_creates_subscriber_if_missing(self, mock_task, client):
        user = User.objects.create_user(email="toggle-new@example.com", password="pw")
        client.force_login(user)
        client.post(reverse("newsletter:toggle_subscription"))
        assert Subscriber.objects.filter(email="toggle-new@example.com", subscribed=True).exists()
        mock_task.delay.assert_called_once()

    def test_toggles_existing_subscriber_off(self, client):
        user = User.objects.create_user(email="toggle-off@example.com", password="pw")
        Subscriber.objects.create(email="toggle-off@example.com", subscribed=True)
        client.force_login(user)
        client.post(reverse("newsletter:toggle_subscription"))
        sub = Subscriber.objects.get(email="toggle-off@example.com")
        assert sub.subscribed is False

    def test_toggles_existing_subscriber_on(self, client):
        user = User.objects.create_user(email="toggle-on@example.com", password="pw")
        Subscriber.objects.create(email="toggle-on@example.com", subscribed=False)
        client.force_login(user)
        client.post(reverse("newsletter:toggle_subscription"))
        sub = Subscriber.objects.get(email="toggle-on@example.com")
        assert sub.subscribed is True

    def test_links_user_to_subscriber(self, client):
        user = User.objects.create_user(email="toggle-link@example.com", password="pw")
        sub = Subscriber.objects.create(email="toggle-link@example.com", subscribed=True, user=None)
        client.force_login(user)
        client.post(reverse("newsletter:toggle_subscription"))
        sub.refresh_from_db()
        assert sub.user == user
