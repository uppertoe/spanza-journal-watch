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

    @patch("spanza_journal_watch.newsletter.views.send_subscription_request_email")
    def test_htmx_post_sends_confirmation_request_and_stores_nothing(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "newsub@example.com"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 302
        assert not Subscriber.objects.filter(email="newsub@example.com").exists()
        mock_task.delay.assert_called_once_with("newsub@example.com", Subscriber.Source.SUBSCRIBE_FORM)

    @patch("spanza_journal_watch.newsletter.views.send_subscription_request_email")
    def test_htmx_post_does_not_resubscribe_until_confirmed(self, mock_task, client):
        sub = Subscriber.objects.create(email="existing@example.com", subscribed=False)
        client.post(reverse("newsletter:subscribe"), {"email": "existing@example.com"}, HTTP_HX_REQUEST="true")
        sub.refresh_from_db()
        assert sub.subscribed is False
        mock_task.delay.assert_called_once()

    @patch("spanza_journal_watch.newsletter.views.send_subscription_request_email")
    def test_already_subscribed_address_gets_same_response_and_no_email(self, mock_task, client):
        Subscriber.objects.create(email="already@example.com", subscribed=True)
        response = client.post(
            reverse("newsletter:subscribe"), {"email": "already@example.com"}, HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 302
        assert not mock_task.delay.called

    @patch("spanza_journal_watch.newsletter.views.send_subscription_request_email")
    def test_repeat_requests_send_one_email(self, mock_task, client, settings):
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "optin"}
        }
        for _ in range(3):
            client.post(reverse("newsletter:subscribe"), {"email": "repeat@example.com"}, HTTP_HX_REQUEST="true")
        assert mock_task.delay.call_count == 1

    @patch("spanza_journal_watch.newsletter.views.send_subscription_request_email")
    def test_htmx_post_does_not_identify_session(self, mock_task, client):
        client.post(reverse("newsletter:subscribe"), {"email": "session@example.com"}, HTTP_HX_REQUEST="true")
        assert client.session.get("subscriber_id") is None
        assert client.session.get("subscribed") is None


class TestConfirmSubscription:
    def _url(self, email="confirm@example.com", source=Subscriber.Source.SUBSCRIBE_FORM):
        from spanza_journal_watch.newsletter.confirmation import make_confirm_token

        return reverse("newsletter:confirm-subscription", args=[make_confirm_token(email, source)])

    def test_get_shows_button_and_subscribes_no_one(self, client):
        response = client.get(self._url())
        assert response.status_code == 200
        assert b"Confirm subscription" in response.content
        assert not Subscriber.objects.filter(email="confirm@example.com").exists()

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_post_subscribes_and_identifies_session(self, mock_task, client):
        response = client.post(self._url())
        assert response.status_code == 302
        subscriber = Subscriber.objects.get(email="confirm@example.com")
        assert subscriber.subscribed is True
        assert subscriber.source == Subscriber.Source.SUBSCRIBE_FORM
        assert client.session.get("subscriber_id") == subscriber.pk
        mock_task.delay.assert_called_once_with(subscriber.pk)

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_post_resubscribes_existing_address(self, mock_task, client):
        sub = Subscriber.objects.create(email="confirm@example.com", subscribed=False)
        client.post(self._url())
        sub.refresh_from_db()
        assert sub.subscribed is True
        assert Subscriber.objects.filter(email="confirm@example.com").count() == 1

    def test_tampered_link_is_refused(self, client):
        response = client.post(self._url() + "x")
        assert response.status_code == 200
        assert b"This confirmation link has expired or is not valid." in response.content
        assert not Subscriber.objects.exists()


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

    def test_toggle_syncs_duplicate_exact_email_records(self, client):
        user = User.objects.create_user(email="toggle-dup@example.com", password="pw")
        primary = Subscriber.objects.create(email="toggle-dup@example.com", subscribed=True)
        duplicate = Subscriber.objects.create(email="TOGGLE-DUP@example.com", subscribed=False)

        client.force_login(user)
        client.post(reverse("newsletter:toggle_subscription"))

        primary.refresh_from_db()
        duplicate.refresh_from_db()
        assert primary.subscribed is False
        assert duplicate.subscribed is False
