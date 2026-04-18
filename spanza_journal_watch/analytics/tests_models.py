"""
Tests for analytics model methods.

Covers:
1. AnalyticsEvent.record_event — creates events, clamps fields,
   and captures landing-page/share-token state
2. Subscriber attachment for analytics events
"""

import pytest
from django.test import RequestFactory

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Journal, Review

pytestmark = pytest.mark.django_db


def _make_review():
    journal = Journal.objects.create(name="AM Journal")
    article = PubmedArticle.objects.create(title="AM Article", journal=journal, active=True)
    review = Review(article=article, body="body", active=True, slug="am-review")
    Review.objects.bulk_create([review])
    return Review.objects.get(slug="am-review")


class _FakeSession(dict):
    """Minimal session-like object for tests that need session_key."""

    session_key = "test-session-key-1234"

    def create(self):
        pass


def _request_with_session(path="/", **headers):
    factory = RequestFactory()
    request = factory.get(path, **headers)
    request.session = _FakeSession()
    request.analytics_visitor_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    return request


class TestAnalyticsEventRecordEvent:
    def test_creates_event_without_request(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        )
        assert event.pk is not None
        assert event.event_type == AnalyticsEvent.EventType.PAGE_VISIT
        assert event.content_object is None

    def test_creates_event_with_content_object(self):
        review = _make_review()
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
            content_object=review,
        )
        assert event.content_object == review

    def test_scroll_depth_clamped_to_100(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
            scroll_depth=150,
        )
        assert event.scroll_depth == 100

    def test_scroll_depth_clamped_to_0(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
            scroll_depth=-10,
        )
        assert event.scroll_depth == 0

    def test_duration_ms_clamped_to_0(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
            duration_ms=-500,
        )
        assert event.duration_ms == 0

    def test_source_truncated_to_64_chars(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            source="x" * 100,
        )
        assert len(event.source) == 64

    def test_session_sequence_defaults_without_extra_count_query(self):
        request = _request_with_session()
        e1 = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        e2 = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        assert e1.session_sequence == 0
        assert e2.session_sequence == 0

    def test_captures_landing_page_from_session(self):
        request = _request_with_session()
        request.session["analytics_landing_page"] = "/search/?q=test"
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        assert event.landing_page == "/search/?q=test"

    def test_captures_share_token_from_session(self):
        request = _request_with_session()
        request.session["analytics_share_token"] = "abc123"
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        assert event.share_token == "abc123"

    def test_metadata_defaults_to_empty_dict(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        )
        assert event.metadata == {}

    def test_metadata_stored(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.SEARCH,
            metadata={"query": "airway"},
        )
        assert event.metadata == {"query": "airway"}

    def test_valid_subscriber_id_attaches_subscriber(self):
        subscriber = Subscriber.objects.create(email="event-sub@example.com", subscribed=True)
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            subscriber_id=subscriber.pk,
        )
        assert event.subscriber == subscriber

    def test_invalid_subscriber_id_does_not_crash(self):
        event = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            subscriber_id=999999,
        )
        assert event.pk is not None
        assert event.subscriber is None
