"""
Tests for analytics views.

Covers:
1. track_email_open — returns PNG pixel, creates NewsletterOpen, sets session
2. track_email_click — redirects, sets subscriber session and newsletter referrer
3. track_newsletter_link — creates NewsletterClick, redirects
4. page_view — preserves hit counts without creating legacy PageView rows
5. track_event — rejects invalid payload, rejects unknown event type
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Hit, Issue, Journal, Review

pytestmark = pytest.mark.django_db


def _make_newsletter_and_subscriber():
    issue = Issue.objects.create(name="AV Issue", body="body", active=True)
    newsletter = Newsletter.objects.create(issue=issue, subject="AV Newsletter", send_date=timezone.now())
    subscriber = Subscriber.objects.create(email="av-reader@example.com", subscribed=True)
    return newsletter, subscriber


def _make_review(slug="av-review"):
    journal = Journal.objects.create(name="AV Journal")
    article = PubmedArticle.objects.create(title="AV Article", journal=journal, active=True)
    review = Review(article=article, body="body", active=True, slug=slug)
    Review.objects.bulk_create([review])
    return Review.objects.get(slug=slug)


# ---------------------------------------------------------------------------
# 1. track_email_open
# ---------------------------------------------------------------------------


class TestTrackEmailOpen:
    def test_returns_png(self, client):
        response = client.get(reverse("analytics:track_email_open"))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"

    def test_creates_newsletter_open_when_valid(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        client.get(
            reverse("analytics:track_email_open"),
            {"email": subscriber.email, "token": newsletter.email_token},
        )
        assert NewsletterOpen.objects.filter(newsletter=newsletter, subscriber=subscriber).exists()

    def test_sets_subscriber_in_session(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        client.get(
            reverse("analytics:track_email_open"),
            {"email": subscriber.email, "token": newsletter.email_token},
        )
        assert client.session.get("subscriber_id") == subscriber.pk

    def test_invalid_token_still_returns_pixel(self, client):
        response = client.get(
            reverse("analytics:track_email_open"),
            {"email": "nobody@example.com", "token": "bad-token"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"

    def test_no_params_still_returns_pixel(self, client):
        response = client.get(reverse("analytics:track_email_open"))
        assert response.status_code == 200
        assert NewsletterOpen.objects.count() == 0


# ---------------------------------------------------------------------------
# 2. track_email_click
# ---------------------------------------------------------------------------


class TestTrackEmailClick:
    def test_redirects_to_next_url(self, client):
        subscriber = Subscriber.objects.create(email="click@example.com", subscribed=True)
        response = client.get(
            reverse("analytics:track_email_click"),
            {"email": subscriber.email, "next": "/reviews/some-review"},
        )
        assert response.status_code == 302

    def test_sets_subscriber_in_session(self, client):
        subscriber = Subscriber.objects.create(email="click-session@example.com", subscribed=True)
        client.get(
            reverse("analytics:track_email_click"),
            {"email": subscriber.email, "next": "/"},
        )
        assert client.session.get("subscriber_id") == subscriber.pk

    def test_missing_subscriber_still_redirects(self, client):
        response = client.get(
            reverse("analytics:track_email_click"),
            {"email": "ghost@example.com", "next": "/"},
        )
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# 3. track_newsletter_link
# ---------------------------------------------------------------------------


class TestTrackNewsletterLink:
    def test_creates_newsletter_click(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        client.get(
            reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
            {"email": subscriber.email, "next": "/"},
        )
        assert NewsletterClick.objects.filter(newsletter=newsletter, subscriber=subscriber).exists()

    def test_stores_destination_url(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        client.get(
            reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
            {"email": subscriber.email, "next": "https://example.com/article"},
        )
        click = NewsletterClick.objects.get(newsletter=newsletter)
        assert click.destination_url == "https://example.com/article"

    def test_redirects(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        response = client.get(
            reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
            {"email": subscriber.email, "next": "https://example.com"},
        )
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# 4. page_view
# ---------------------------------------------------------------------------


class TestPageView:
    def test_review_page_view_increments_hit(self, client):
        review = _make_review(slug="pv-hit-review")
        client.get(
            reverse("analytics:page_view", kwargs={"model": "review", "slug": "pv-hit-review"}),
            HTTP_USER_AGENT="Mozilla/5.0 Test",
        )
        assert Hit.objects.filter(object_id=review.pk).exists()

    def test_nonexistent_slug_returns_empty_200(self, client):
        response = client.get(
            reverse("analytics:page_view", kwargs={"model": "review", "slug": "nonexistent-review"}),
        )
        assert response.status_code == 200
        assert response.content == b""

    def test_unknown_model_returns_empty_200(self, client):
        response = client.get(
            reverse("analytics:page_view", kwargs={"model": "unknown", "slug": "whatever"}),
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. track_event — edge cases
# ---------------------------------------------------------------------------


class TestTrackEventEdgeCases:
    def test_invalid_json_returns_400(self, client):
        response = client.post(
            reverse("analytics:track_event"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_unknown_event_type_returns_400(self, client):
        response = client.post(
            reverse("analytics:track_event"),
            data={"event_type": "not_a_real_event"},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_empty_event_type_returns_400(self, client):
        response = client.post(
            reverse("analytics:track_event"),
            data={"event_type": ""},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_review_id_returns_400(self, client):
        response = client.post(
            reverse("analytics:track_event"),
            data={"event_type": "review_open", "review_id": 999999},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_get_not_allowed(self, client):
        response = client.get(reverse("analytics:track_event"))
        assert response.status_code == 405
