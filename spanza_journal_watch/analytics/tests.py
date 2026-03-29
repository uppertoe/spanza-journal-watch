from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.analytics.utils import (
    NEWSLETTER_AUTOMATION_WINDOW,
    classify_event_confidence,
    is_probable_automated_newsletter_event,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Article, Author, Issue, Journal, Review

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
    article = Article.objects.create(
        name="Analytics Review Article",
        journal=journal,
        citation="Citation text",
        url="https://example.com/full-text",
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


def test_site_analytics_dashboard_renders(client):
    review = _make_review()
    AnalyticsEvent.record_event(
        event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
        content_object=review,
        source="review_detail",
    )

    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        email="analytics@example.com",
        password="password123",
    )
    client.force_login(user)

    response = client.get(reverse("backend:site_analytics"))

    assert response.status_code == 200
    assert "Site analytics" in response.content.decode("utf-8")


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
