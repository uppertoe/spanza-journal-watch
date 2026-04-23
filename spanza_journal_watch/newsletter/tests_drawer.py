"""
Tests for the drawer-subscribe flow and the jw_sub cookie.

Covers:
1. Drawer subscribe endpoint returns a success fragment and sets jw_sub.
2. Non-drawer subscribe still redirects and sets jw_sub.
3. Newsletter click views set jw_sub when a subscriber is resolved.
4. SubscriberCookieMiddleware sets jw_sub for authenticated subscribers.
5. Context processor reports is_known_subscriber correctly.
6. Drawer template renders correct state for anon vs known-subscriber cookie.
7. Masthead button label swaps (Subscribe ↔ Profile) with cookie state.
8. Welcome email nudges signup for anonymous subscribers only.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.urls import reverse

from spanza_journal_watch.newsletter.context_processors import subscriber_state
from spanza_journal_watch.newsletter.cookies import JW_SUB_COOKIE_NAME
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Issue

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. Drawer subscribe endpoint
# ---------------------------------------------------------------------------


class TestDrawerSubscribe:
    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_drawer_post_returns_success_fragment(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe") + "?source=drawer",
            {"email": "drawer@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"You're subscribed" in response.content
        assert b"drawer@example.com" in response.content
        assert Subscriber.objects.filter(email="drawer@example.com").exists()

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_drawer_post_sets_jw_sub_cookie(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "cookie-drawer@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response.cookies[JW_SUB_COOKIE_NAME].value == "1"

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_drawer_get_returns_form_fragment(self, mock_task, client):
        response = client.get(
            reverse("newsletter:subscribe") + "?source=drawer",
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"drawer-subscribe-container" in response.content

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_nondrawer_post_sets_jw_sub_cookie_and_redirects(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "legacy@example.com"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 302
        assert response.cookies[JW_SUB_COOKIE_NAME].value == "1"


# ---------------------------------------------------------------------------
# 2. Newsletter click views set cookie
# ---------------------------------------------------------------------------


class TestNewsletterClickSetsCookie:
    def test_track_email_click_sets_cookie_for_known_subscriber(self, client):
        Subscriber.objects.create(email="click@example.com", subscribed=True)
        response = client.get(
            reverse("analytics:track_email_click") + "?email=click@example.com&next=/",
        )
        assert response.status_code == 302
        assert response.cookies[JW_SUB_COOKIE_NAME].value == "1"

    def test_track_email_click_skips_cookie_when_no_subscriber(self, client):
        response = client.get(
            reverse("analytics:track_email_click") + "?email=unknown@example.com&next=/",
        )
        assert response.status_code == 302
        assert JW_SUB_COOKIE_NAME not in response.cookies

    def test_track_newsletter_link_sets_cookie(self, client):
        issue = Issue.objects.create(name="Drawer issue", slug="drawer-issue", body="")
        subscriber = Subscriber.objects.create(email="nl@example.com", subscribed=True)
        newsletter = Newsletter.objects.create(issue=issue, subject="Test")

        response = client.get(
            reverse(
                "analytics:track_newsletter_email_link",
                kwargs={"newsletter_token": newsletter.email_token},
            )
            + f"?email={subscriber.email}&next=/",
        )
        assert response.status_code == 302
        assert response.cookies[JW_SUB_COOKIE_NAME].value == "1"


# ---------------------------------------------------------------------------
# 3. SubscriberCookieMiddleware
# ---------------------------------------------------------------------------


class TestSubscriberCookieMiddleware:
    def test_sets_cookie_for_authenticated_subscriber(self, client):
        user = User.objects.create_user(email="mid@example.com", password="pw")
        Subscriber.objects.create(email="mid@example.com", subscribed=True, user=user)
        client.force_login(user)
        response = client.get("/")
        assert response.cookies.get(JW_SUB_COOKIE_NAME)
        assert response.cookies[JW_SUB_COOKIE_NAME].value == "1"

    def test_skips_cookie_for_authenticated_non_subscriber(self, client):
        user = User.objects.create_user(email="nosub@example.com", password="pw")
        client.force_login(user)
        response = client.get("/")
        assert JW_SUB_COOKIE_NAME not in response.cookies

    def test_skips_cookie_when_already_set(self, client):
        user = User.objects.create_user(email="warm@example.com", password="pw")
        Subscriber.objects.create(email="warm@example.com", subscribed=True, user=user)
        client.force_login(user)
        client.cookies[JW_SUB_COOKIE_NAME] = "1"
        response = client.get("/")
        # Should not re-emit the cookie on the response
        assert JW_SUB_COOKIE_NAME not in response.cookies

    def test_skips_cookie_for_anonymous(self, client):
        response = client.get("/")
        assert JW_SUB_COOKIE_NAME not in response.cookies


# ---------------------------------------------------------------------------
# 4. Context processor
# ---------------------------------------------------------------------------


class TestSubscriberStateContextProcessor:
    def test_authenticated_user_is_known_subscriber(self):
        request = RequestFactory().get("/")
        request.user = type("AuthedUser", (), {"is_authenticated": True})()
        request.COOKIES = {}
        assert subscriber_state(request) == {"is_known_subscriber": True}

    def test_anon_with_cookie_is_known(self):
        request = RequestFactory().get("/")
        request.user = type("Anon", (), {"is_authenticated": False})()
        request.COOKIES = {JW_SUB_COOKIE_NAME: "1"}
        assert subscriber_state(request) == {"is_known_subscriber": True}

    def test_anon_without_cookie_is_unknown(self):
        request = RequestFactory().get("/")
        request.user = type("Anon", (), {"is_authenticated": False})()
        request.COOKIES = {}
        assert subscriber_state(request) == {"is_known_subscriber": False}


# ---------------------------------------------------------------------------
# 5. Drawer template rendering
# ---------------------------------------------------------------------------


class TestDrawerTemplate:
    def test_anonymous_sees_subscribe_section(self, client):
        response = client.get("/")
        content = response.content.decode()
        assert "drawer-subscribe-container" in content
        assert "Reviews in your inbox every few months" in content
        assert "Want more?" in content

    def test_authenticated_sees_profile_sections(self, client):
        user = User.objects.create_user(email="dt@example.com", password="pw")
        client.force_login(user)
        response = client.get("/")
        content = response.content.decode()
        assert "Log out" in content
        assert "CPD Report" in content
        # Should not show the anonymous subscribe marketing
        assert "Reviews in your inbox every few months" not in content
        assert "Want more?" not in content


# ---------------------------------------------------------------------------
# 6. Masthead button state
# ---------------------------------------------------------------------------


class TestMastheadButton:
    def test_anon_shows_subscribe_label(self, client):
        response = client.get("/")
        content = response.content.decode()
        assert 'aria-label="Subscribe"' in content
        assert ">Subscribe<" in content
        assert "#icon-email" in content

    def test_anon_with_cookie_shows_profile_label(self, client):
        client.cookies[JW_SUB_COOKIE_NAME] = "1"
        response = client.get("/")
        content = response.content.decode()
        assert 'aria-label="User menu"' in content
        assert ">Profile<" in content

    def test_authenticated_shows_profile_label(self, client):
        user = User.objects.create_user(email="mb@example.com", password="pw")
        client.force_login(user)
        response = client.get("/")
        content = response.content.decode()
        assert 'aria-label="User menu"' in content
        assert ">Profile<" in content


# ---------------------------------------------------------------------------
# 7. Welcome email nudge
# ---------------------------------------------------------------------------


class TestWelcomeEmailNudge:
    @override_settings(DEBUG=True)
    def test_anonymous_subscriber_gets_create_account_link(self):
        subscriber = Subscriber.objects.create(email="anon-welcome@example.com", subscribed=True)
        email = subscriber.generate_confirmation_email()
        txt_body = email.body
        html_body = email.alternatives[0][0]
        assert "Create an account" in html_body
        assert "Want more than the newsletter" in html_body
        assert "Want more than the newsletter" in txt_body
        assert "accounts/signup" in txt_body

    @override_settings(DEBUG=True)
    def test_linked_user_subscriber_skips_nudge(self):
        user = User.objects.create_user(email="linked@example.com", password="pw")
        subscriber = Subscriber.objects.create(email="linked@example.com", subscribed=True, user=user)
        email = subscriber.generate_confirmation_email()
        txt_body = email.body
        html_body = email.alternatives[0][0]
        assert "Create an account" not in html_body
        assert "Want more than the newsletter" not in txt_body


# ---------------------------------------------------------------------------
# 8. Drawer subscribe returns OOB masthead swap
# ---------------------------------------------------------------------------


class TestDrawerSubscribeOOBSwap:
    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_drawer_success_includes_oob_masthead(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe") + "?source=drawer",
            {"email": "oob@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        # OOB masthead fragment
        assert 'id="masthead-profile-button"' in content
        assert 'hx-swap-oob="true"' in content
        # Post-subscribe state should show Profile, not Subscribe
        assert ">Profile<" in content
        assert 'aria-label="User menu"' in content

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_nondrawer_success_does_not_include_oob(self, mock_task, client):
        response = client.post(
            reverse("newsletter:subscribe"),
            {"email": "classic@example.com"},
            HTTP_HX_REQUEST="true",
        )
        # Classic path redirects; no OOB fragment to carry
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# 9. Subscribed-drawer panel rendering
# ---------------------------------------------------------------------------


class TestSubscribedDrawerPanel:
    def test_anon_cookie_only_shows_subscribed_panel_without_unsubscribe(self, client):
        client.cookies[JW_SUB_COOKIE_NAME] = "1"
        response = client.get("/")
        content = response.content.decode()
        assert "You're subscribed" in content
        # No session-bound subscriber → no unsubscribe button in drawer
        assert "Unsubscribe" not in content
        # Falls back to generic email-link guidance
        assert "link in any of our emails" in content

    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_post_subscribe_session_shows_email_and_unsubscribe(self, mock_task, client):
        # Subscribe via drawer to populate session
        client.post(
            reverse("newsletter:subscribe") + "?source=drawer",
            {"email": "session@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        # Reopen the page as the same client
        response = client.get("/")
        content = response.content.decode()
        assert "You're subscribed" in content
        assert "session@example.com" in content
        assert "Unsubscribe" in content


# ---------------------------------------------------------------------------
# 10. Drawer unsubscribe view
# ---------------------------------------------------------------------------


class TestDrawerUnsubscribe:
    @patch("spanza_journal_watch.newsletter.views.send_confirmation_email")
    def test_drawer_unsubscribe_clears_state(self, mock_task, client):
        # First subscribe via drawer
        client.post(
            reverse("newsletter:subscribe") + "?source=drawer",
            {"email": "bye@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        subscriber = Subscriber.objects.get(email="bye@example.com")
        assert subscriber.subscribed is True

        response = client.post(
            reverse("newsletter:drawer_unsubscribe"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Subscriber flipped
        subscriber.refresh_from_db()
        assert subscriber.subscribed is False
        # Session cleared
        assert "subscriber_id" not in client.session
        assert "subscribed" not in client.session
        # Cookie deleted (set to empty with max-age=0)
        assert client.cookies[JW_SUB_COOKIE_NAME].value == ""
        # Response shows resubscribe form + OOB masthead in Subscribe state
        assert "You've been unsubscribed" in content
        assert 'id="masthead-profile-button"' in content
        assert 'hx-swap-oob="true"' in content
        assert ">Subscribe<" in content

    def test_drawer_unsubscribe_requires_htmx(self, client):
        response = client.post(reverse("newsletter:drawer_unsubscribe"))
        assert response.status_code == 400

    def test_drawer_unsubscribe_without_session_still_renders(self, client):
        response = client.post(
            reverse("newsletter:drawer_unsubscribe"),
            HTTP_HX_REQUEST="true",
        )
        # No session subscriber, but still returns a valid fragment
        assert response.status_code == 200
        assert b"You've been unsubscribed" in response.content


# ---------------------------------------------------------------------------
# 10a. Email-link unsubscribe clears drawer state
# ---------------------------------------------------------------------------


class TestEmailUnsubscribeClearsDrawerState:
    def test_confirm_unsubscribe_deletes_cookie_and_session(self, client):
        subscriber = Subscriber.objects.create(email="email-unsub@example.com", subscribed=True)
        # Simulate a prior subscribed browser session
        client.cookies[JW_SUB_COOKIE_NAME] = "1"
        session = client.session
        session["subscriber_id"] = subscriber.pk
        session["subscriber_email"] = subscriber.email
        session["subscribed"] = True
        session.save()

        response = client.post(
            reverse("newsletter:confirm-unsubscribe", kwargs={"unsubscribe_token": subscriber.unsubscribe_token}),
        )
        assert response.status_code == 302
        assert response.cookies[JW_SUB_COOKIE_NAME].value == ""
        subscriber.refresh_from_db()
        assert subscriber.subscribed is False
        assert "subscriber_id" not in client.session
        assert "subscriber_email" not in client.session
        assert "subscribed" not in client.session

    def test_one_click_unsubscribe_post_deletes_cookie(self, client):
        subscriber = Subscriber.objects.create(email="one-click@example.com", subscribed=True)
        client.cookies[JW_SUB_COOKIE_NAME] = "1"

        response = client.post(
            reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": subscriber.unsubscribe_token}),
        )
        assert response.status_code == 200
        assert response.cookies[JW_SUB_COOKIE_NAME].value == ""
        subscriber.refresh_from_db()
        assert subscriber.subscribed is False


# ---------------------------------------------------------------------------
# 11. Copy: cadence wording
# ---------------------------------------------------------------------------


class TestCadenceCopy:
    def test_drawer_form_says_few_months(self, client):
        response = client.get("/")
        content = response.content.decode()
        assert "every few months" in content
        assert "every two months" not in content

    @override_settings(DEBUG=True)
    def test_welcome_email_says_few_months(self):
        subscriber = Subscriber.objects.create(email="cadence@example.com", subscribed=True)
        email = subscriber.generate_confirmation_email()
        html_body = email.alternatives[0][0]
        txt_body = email.body
        assert "every few months" in html_body
        assert "every two months" not in html_body
        assert "every few months" in txt_body
        assert "every two months" not in txt_body
