from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.analytics.utils import (
    NEWSLETTER_AUTOMATION_WINDOW,
    is_probable_automated_newsletter_event,
)
from spanza_journal_watch.newsletter.models import Newsletter
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
    review = Review.objects.create(
        article=article,
        author=author,
        body="### Summary\n\nThis is an analytics test review body.",
        active=True,
    )
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


def test_search_page_records_search_analytics_event(client):
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
