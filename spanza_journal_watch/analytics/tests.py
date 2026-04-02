import datetime
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.analytics.utils import (
    _REFERRER_SESSION_KEY,
    NEWSLETTER_AUTOMATION_WINDOW,
    REFERRER_DIRECT,
    REFERRER_INTERNAL,
    REFERRER_NEWSLETTER,
    REFERRER_OTHER,
    REFERRER_SEARCH,
    REFERRER_SOCIAL,
    _categorize_from_header,
    categorize_referrer,
    classify_event_confidence,
    is_probable_automated_event,
    is_probable_automated_newsletter_event,
    set_newsletter_referrer_in_session,
)
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review

pytestmark = pytest.mark.django_db


def _make_newsletter(send_date):
    issue = Issue.objects.create(name="Analytics Issue", body="Issue body")
    return Newsletter.objects.create(
        issue=issue,
        subject="Analytics newsletter",
        send_date=send_date,
    )


def test_newsletter_event_immediately_after_send_is_probably_automated():
    newsletter = _make_newsletter(send_date=timezone.now())
    request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")

    assert is_probable_automated_newsletter_event(request, newsletter) is True


def _make_review():
    journal = Journal.objects.create(name="Analytics Journal")
    author = Author.objects.create(name="Analytics Author")
    article = PubmedArticle.objects.create(
        title="Analytics Review Article",
        journal=journal,
        citation="Citation text",
        article_url="https://example.com/full-text",
        active=True,
    )
    review = Review(
        article=article,
        author=author,
        slug="analytics-review",
        body="### Summary\n\nThis is an analytics test review body.",
        active=True,
        publish_date=timezone.now(),
    )
    Review.objects.bulk_create([review])
    review = Review.objects.get(slug="analytics-review")
    issue = Issue.objects.create(name="Analytics Issue", body="Issue body", active=True)
    issue.reviews.add(review)
    return review


def test_track_event_records_review_share_event(client):
    review = _make_review()

    response = client.post(
        reverse("analytics:track_event"),
        data={
            "event_type": AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
            "review_id": review.pk,
            "source": "review_detail",
            "duration_ms": 12000,
            "scroll_depth": 64,
            "metadata": {"query": "airway"},
        },
        content_type="application/json",
        HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    )

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get()
    assert event.event_type == AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK
    assert event.content_object == review
    assert event.source == "review_detail"
    assert event.duration_ms == 12000
    assert event.scroll_depth == 64
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_track_event_with_subscriber_session_records_known_subscriber_confidence(client):
    review = _make_review()
    subscriber = Subscriber.objects.create(email="reader@example.com", subscribed=True)
    session = client.session
    session["subscriber_id"] = subscriber.pk
    session.save()

    response = client.post(
        reverse("analytics:track_event"),
        data={
            "event_type": AnalyticsEvent.EventType.REVIEW_OPEN,
            "review_id": review.pk,
            "source": "review_detail",
        },
        content_type="application/json",
        HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    )

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)
    assert event.subscriber == subscriber
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN


def test_search_page_records_search_analytics_event(client):
    if connection.vendor != "postgresql":
        pytest.skip("Search analytics route test requires PostgreSQL full-text search support.")

    _make_review()

    response = client.get(reverse("submissions:search"), {"q": "analytics"})

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.SEARCH)
    assert event.metadata["query"] == "analytics"
    assert event.source == "search_page"


def test_site_analytics_redirects_to_overview(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        email="analytics@example.com",
        password="password123",
    )
    client.force_login(user)

    response = client.get(reverse("backend:site_analytics"))

    assert response.status_code == 302
    assert "/analytics/overview/" in response["Location"]


def test_newsletter_event_long_after_send_is_not_automated_without_markers():
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW - timedelta(seconds=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")

    assert is_probable_automated_newsletter_event(request, newsletter) is False


def test_newsletter_event_with_scanner_user_agent_is_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - timedelta(hours=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="GoogleImageProxy")

    assert is_probable_automated_newsletter_event(request, newsletter) is True


def test_classify_event_confidence_prioritises_automation_over_subscriber():
    subscriber = Subscriber.objects.create(email="subscriber@example.com", subscribed=True)

    assert (
        classify_event_confidence(automated=True, subscriber=subscriber)
        == AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED
    )


# ---- Visitor ID Middleware ----


def test_visitor_id_cookie_set_on_first_visit(client):
    response = client.get("/")
    assert "jwvid" in response.cookies
    cookie = response.cookies["jwvid"]
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "lax"


def test_visitor_id_cookie_not_reset_on_second_visit(client):
    r1 = client.get("/")
    first_value = r1.cookies["jwvid"].value
    r2 = client.get("/")
    # On second visit the browser sends the cookie, so server should not set a new one
    if "jwvid" in r2.cookies:
        assert r2.cookies["jwvid"].value == first_value


# ---- Referrer categorisation ----


def test_empty_referer_is_direct():
    assert _categorize_from_header("") == REFERRER_DIRECT


def test_none_referer_is_direct():
    assert _categorize_from_header(None) == REFERRER_DIRECT


def test_google_referer_is_search():
    assert _categorize_from_header("https://www.google.com/search?q=anaesthesia") == REFERRER_SEARCH


def test_google_au_referer_is_search():
    assert _categorize_from_header("https://www.google.com.au/search?q=test") == REFERRER_SEARCH


def test_bing_referer_is_search():
    assert _categorize_from_header("https://www.bing.com/search?q=paediatric") == REFERRER_SEARCH


def test_x_com_referer_is_social():
    assert _categorize_from_header("https://x.com/some/link") == REFERRER_SOCIAL


def test_bluesky_referer_is_social():
    assert _categorize_from_header("https://bsky.app/profile/user") == REFERRER_SOCIAL


def test_facebook_referer_is_social():
    assert _categorize_from_header("https://www.facebook.com/") == REFERRER_SOCIAL


def test_unknown_external_is_other():
    assert _categorize_from_header("https://hospital.example.org/links") == REFERRER_OTHER


def test_own_domain_is_internal():
    assert _categorize_from_header("https://example.com/reviews/slug", own_domain="example.com") == REFERRER_INTERNAL


def test_newsletter_session_wins_over_google_referer(rf):
    request = rf.get("/", HTTP_REFERER="https://www.google.com/search?q=test")
    tomorrow = timezone.localtime().date() + datetime.timedelta(days=1)
    expires = timezone.make_aware(
        datetime.datetime.combine(tomorrow, datetime.time.min),
        timezone.get_current_timezone(),
    )
    request.session = {_REFERRER_SESSION_KEY: {"category": REFERRER_NEWSLETTER, "expires": expires.isoformat()}}
    assert categorize_referrer(request) == REFERRER_NEWSLETTER


def test_expired_newsletter_session_falls_back_to_header(rf):
    request = rf.get("/", HTTP_REFERER="https://www.google.com/search?q=test")
    yesterday = timezone.localtime() - datetime.timedelta(days=1)
    request.session = {_REFERRER_SESSION_KEY: {"category": REFERRER_NEWSLETTER, "expires": yesterday.isoformat()}}
    assert categorize_referrer(request) == REFERRER_SEARCH


def test_set_newsletter_referrer_expires_at_local_midnight(rf):
    request = rf.get("/")
    request.session = {}
    set_newsletter_referrer_in_session(request)
    entry = request.session.get(_REFERRER_SESSION_KEY)
    assert entry is not None
    assert entry["category"] == REFERRER_NEWSLETTER
    expires = datetime.datetime.fromisoformat(entry["expires"])
    # Should expire sometime after now but before 2 days from now
    assert timezone.now() < expires
    assert expires < timezone.now() + datetime.timedelta(days=2)


# ---- Bot filtering ----


def test_empty_user_agent_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="")
    assert is_probable_automated_event(request) is True


def test_semrushbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; SemrushBot/7~bl)")
    assert is_probable_automated_event(request) is True


def test_facebookexternalhit_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="facebookexternalhit/1.1")
    assert is_probable_automated_event(request) is True


def test_twitterbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Twitterbot/1.0")
    assert is_probable_automated_event(request) is True


def test_ahrefsbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; AhrefsBot/7.0)")
    assert is_probable_automated_event(request) is True


def test_normal_browser_not_automated(rf):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0"
    request = rf.get("/", HTTP_USER_AGENT=ua)
    assert is_probable_automated_event(request) is False


# ---- Newsletter automation window (60s) ----


def test_newsletter_event_at_exactly_60s_is_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - timedelta(seconds=59))
    request = RequestFactory().get(
        "/", HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    assert is_probable_automated_newsletter_event(request, newsletter) is True


def test_newsletter_event_after_60s_is_not_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW - timedelta(seconds=1))
    request = RequestFactory().get(
        "/", HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    assert is_probable_automated_newsletter_event(request, newsletter) is False


# ---- New event types recorded ----


def test_journal_browser_visit_event_accepted(client):
    _make_review()
    response = client.post(
        reverse("analytics:track_event"),
        data={"event_type": AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT).exists()


def test_journal_article_interact_event_accepted(client):
    _make_review()
    response = client.post(
        reverse("analytics:track_event"),
        data={"event_type": AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT, "metadata": {"action": "star"}},
        content_type="application/json",
    )
    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT)
    assert event.metadata["action"] == "star"


# ---- Analytics pages auth ----


def test_analytics_overview_requires_view_site_analytics(client):
    User = get_user_model()
    user = User.objects.create_user(email="noanalytics@example.com", password="pw")
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(reverse("backend:analytics_overview"))
    assert response.status_code == 403


def test_analytics_overview_accessible_with_view_site_analytics(client):
    User = get_user_model()
    user = User.objects.create_superuser(email="analytics2@example.com", password="pw")
    client.force_login(user)
    response = client.get(reverse("backend:analytics_overview"))
    assert response.status_code == 200


def test_analytics_email_requires_view_newsletter_stats(client):
    User = get_user_model()
    user = User.objects.create_user(email="noemail@example.com", password="pw")
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(reverse("backend:analytics_email"))
    assert response.status_code == 403
