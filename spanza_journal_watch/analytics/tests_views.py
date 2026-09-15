"""
Tests for analytics views.

Covers:
1. track_email_open — returns PNG pixel, creates NewsletterOpen, sets session
2. track_email_click — redirects, sets subscriber session and newsletter referrer
3. track_newsletter_link — creates NewsletterClick, redirects
4. page_view — preserves hit counts without creating legacy PageView rows
5. track_event — rejects invalid payload, rejects unknown event type
"""

from urllib.parse import parse_qsl, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.links import EmailLinkBuilder
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


def _query(url):
    """The query string of a link built by EmailLinkBuilder, as the test client wants it."""
    return dict(parse_qsl(urlparse(url).query))


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
    def test_signed_link_redirects_to_destination(self, client):
        subscriber = Subscriber.objects.create(email="click@example.com", subscribed=True)
        link = EmailLinkBuilder(subscriber, domain="").url("https://www.spanza.org.au/events/")
        response = client.get(reverse("analytics:track_email_click"), _query(link))
        assert response.status_code == 302
        assert response["Location"] == "https://www.spanza.org.au/events/"
        assert subscriber.email not in link

    def test_unsigned_external_destination_is_refused(self, client):
        subscriber = Subscriber.objects.create(email="click-open@example.com", subscribed=True)
        response = client.get(
            reverse("analytics:track_email_click"),
            {"t": subscriber.tracking_token, "next": "https://evil.example/phish"},
        )
        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_tampered_signature_is_refused(self, client):
        subscriber = Subscriber.objects.create(email="click-tamper@example.com", subscribed=True)
        params = _query(EmailLinkBuilder(subscriber, domain="").url("https://www.spanza.org.au/"))
        params["u"] = "https://evil.example/"
        response = client.get(reverse("analytics:track_email_click"), params)
        assert response["Location"] == "/"

    def test_signature_is_bound_to_the_subscriber(self, client):
        alice = Subscriber.objects.create(email="alice@example.com", subscribed=True)
        bob = Subscriber.objects.create(email="bob@example.com", subscribed=True)
        params = _query(EmailLinkBuilder(alice, domain="").url("https://www.spanza.org.au/"))
        params["t"] = bob.tracking_token
        response = client.get(reverse("analytics:track_email_click"), params)
        assert response["Location"] == "/"
        assert client.session.get("subscriber_id") is None

    def test_legacy_email_link_still_follows_local_paths(self, client):
        subscriber = Subscriber.objects.create(email="click@example.com", subscribed=True)
        response = client.get(
            reverse("analytics:track_email_click"),
            {"email": subscriber.email, "next": "/reviews/some-review"},
        )
        assert response.status_code == 302
        assert response["Location"] == "/reviews/some-review"
        assert client.session.get("subscriber_id") == subscriber.pk

    def test_legacy_link_to_a_known_article_is_followed(self, client):
        subscriber = Subscriber.objects.create(email="click-article@example.com", subscribed=True)
        journal = Journal.objects.create(name="Legacy Journal")
        PubmedArticle.objects.create(
            title="Legacy", journal=journal, active=True, article_url="https://publisher.example/paper"
        )
        response = client.get(
            reverse("analytics:track_email_click"),
            {"email": subscriber.email, "next": "https://publisher.example/paper"},
        )
        assert response["Location"] == "https://publisher.example/paper"

    def test_legacy_link_to_an_unknown_path_lands_on_home(self, client):
        subscriber = Subscriber.objects.create(email="click-404@example.com", subscribed=True)
        response = client.get(
            reverse("analytics:track_email_click"),
            {"email": subscriber.email, "next": "/no/such/page/"},
        )
        assert response["Location"] == "/"

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

    def test_signed_link_stores_destination_and_redirects(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        link = EmailLinkBuilder(subscriber, newsletter_token=newsletter.email_token, domain="").url(
            "https://example.com/article"
        )
        response = client.get(
            reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
            _query(link),
        )
        assert response.status_code == 302
        assert response["Location"] == "https://example.com/article"
        click = NewsletterClick.objects.get(newsletter=newsletter, subscriber=subscriber)
        assert click.destination_url == "https://example.com/article"

    def test_unsigned_external_destination_is_refused(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        response = client.get(
            reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
            {"t": subscriber.tracking_token, "next": "https://example.com/article"},
        )
        assert response["Location"] == "/"
        click = NewsletterClick.objects.get(newsletter=newsletter, subscriber=subscriber)
        assert click.destination_url == "/"

    def test_pixel_identifies_subscriber_by_tracking_token(self, client):
        newsletter, subscriber = _make_newsletter_and_subscriber()
        pixel = EmailLinkBuilder(subscriber, newsletter_token=newsletter.email_token, domain="").pixel_url()
        assert subscriber.email not in pixel
        client.get(reverse("analytics:track_email_open"), _query(pixel))
        assert NewsletterOpen.objects.filter(newsletter=newsletter, subscriber=subscriber).exists()


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
