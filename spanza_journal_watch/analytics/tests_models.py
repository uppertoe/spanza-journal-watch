"""
Tests for analytics model methods.

Covers:
1. PageView.record_view — creates PageView with correct fields, handles no request
2. PageView._get_subscriber — returns subscriber, handles missing/invalid
3. AnalyticsEvent.record_event — creates event, clamps scroll_depth/duration_ms,
   computes session_sequence, captures landing_page and share_token
"""

import pytest
from django.test import RequestFactory

from spanza_journal_watch.analytics.models import AnalyticsEvent, PageView
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Issue, Journal, Review

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue():
    return Issue.objects.create(name="Analytics Model Issue", body="body", active=True)


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
    """Return a request with a real-ish session dict."""
    factory = RequestFactory()
    request = factory.get(path, **headers)
    request.session = _FakeSession()
    request.analytics_visitor_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    return request


# ---------------------------------------------------------------------------
# 1. PageView.record_view
# ---------------------------------------------------------------------------


class TestPageViewRecordView:
    def test_creates_page_view(self):
        issue = _make_issue()
        PageView.record_view(issue)

        pv = PageView.objects.filter(object_id=issue.pk).first()
        assert pv is not None
        assert pv.content_object == issue

    def test_with_request_captures_user_agent(self):
        issue = _make_issue()
        request = _request_with_session(
            HTTP_USER_AGENT="Mozilla/5.0 Test Browser",
        )
        PageView.record_view(issue, request=request)

        pv = PageView.objects.filter(object_id=issue.pk).latest("timestamp")
        assert pv.user_agent == "Mozilla/5.0 Test Browser"

    def test_with_request_captures_visitor_id(self):
        issue = _make_issue()
        request = _request_with_session()
        PageView.record_view(issue, request=request)

        pv = PageView.objects.filter(object_id=issue.pk).latest("timestamp")
        assert str(pv.visitor_id) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_with_subscriber_id(self):
        issue = _make_issue()
        sub = Subscriber.objects.create(email="pv-sub@example.com", subscribed=True)
        PageView.record_view(issue, subscriber_id=sub.pk)

        pv = PageView.objects.filter(object_id=issue.pk).latest("timestamp")
        assert pv.subscriber == sub

    def test_invalid_subscriber_id_does_not_crash(self):
        issue = _make_issue()
        PageView.record_view(issue, subscriber_id=999999)

        pv = PageView.objects.filter(object_id=issue.pk).first()
        assert pv is not None
        assert pv.subscriber is None

    def test_without_request_defaults(self):
        issue = _make_issue()
        PageView.record_view(issue)

        pv = PageView.objects.filter(object_id=issue.pk).first()
        assert pv.user_agent == ""
        assert pv.automated is False
        assert pv.visitor_id is None


# ---------------------------------------------------------------------------
# 2. PageView._get_subscriber
# ---------------------------------------------------------------------------


class TestGetSubscriber:
    def test_returns_subscriber(self):
        sub = Subscriber.objects.create(email="gs@example.com", subscribed=True)
        result = PageView._get_subscriber(sub.pk, log_context="test")
        assert result == sub

    def test_returns_none_for_missing(self):
        result = PageView._get_subscriber(999999, log_context="test")
        assert result is None

    def test_returns_none_for_none(self):
        result = PageView._get_subscriber(None, log_context="test")
        assert result is None

    def test_returns_none_for_zero(self):
        result = PageView._get_subscriber(0, log_context="test")
        assert result is None


# ---------------------------------------------------------------------------
# 3. AnalyticsEvent.record_event
# ---------------------------------------------------------------------------


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

    def test_session_sequence_increments(self):
        request = _request_with_session()
        e1 = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        e2 = AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
        )
        assert e1.session_sequence == 1
        assert e2.session_sequence == 2

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
