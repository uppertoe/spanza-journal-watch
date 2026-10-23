"""Counting logic behind the analytics dashboards (the October 2026 accuracy audit)."""

import datetime
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent, AutomatedRequestCount
from spanza_journal_watch.backend.analytics_views import benchmarks, common, overview, visits
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review

pytestmark = pytest.mark.django_db

E = AnalyticsEvent.EventType


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def analyst(client):
    user = get_user_model().objects.create_superuser(email="analyst@example.com", password="pw")
    client.force_login(user)
    return client


def _local(day, hour=0, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


def _event(event_type=E.PAGE_VISIT, *, when=None, **fields):
    fields.setdefault("automated", False)
    event = AnalyticsEvent.objects.create(event_type=event_type, **fields)
    if when is not None:
        AnalyticsEvent.objects.filter(pk=event.pk).update(timestamp=when)
    return event


def _freeze(monkeypatch, moment):
    monkeypatch.setattr(timezone, "now", lambda: moment)


# ---- 1. Engaged humans ----


class TestEngagedVisitors:
    def test_subscriber_only_scanner_visitor_is_not_engaged(self):
        sub = Subscriber.objects.create(email="scanner@example.com")
        scanner = uuid.uuid4()
        _event(visitor_id=scanner, subscriber=sub, js_verified=False)

        qs = AnalyticsEvent.objects.filter(automated=False)
        assert common.engaged_visitor_ids(qs) == set()
        assert common._engaged_human_count(qs) == 0

    def test_subscriber_with_a_js_event_is_engaged(self):
        sub = Subscriber.objects.create(email="reader@example.com")
        reader = uuid.uuid4()
        _event(visitor_id=reader, subscriber=sub)  # the newsletter click, no JS
        _event(E.REVIEW_OPEN, visitor_id=reader, js_verified=True)  # a later JS beacon

        qs = AnalyticsEvent.objects.filter(automated=False)
        assert common.engaged_visitor_ids(qs) == {reader}
        assert common._engaged_human_count(qs) == 1

    def test_deliberate_event_is_engaged_without_subscriber_or_js(self):
        clicker = uuid.uuid4()
        js_only = uuid.uuid4()
        _event(E.REVIEW_FULL_TEXT_CLICK, visitor_id=clicker)
        _event(E.REVIEW_ENGAGED, visitor_id=js_only, js_verified=True)  # dwell alone is gameable

        assert common.engaged_visitor_ids(AnalyticsEvent.objects.all()) == {clicker}

    def test_visit_needs_js_alongside_a_subscriber_to_be_deliberate(self):
        sub = Subscriber.objects.create(email="visit@example.com")
        scanner, reader = uuid.uuid4(), uuid.uuid4()
        now = timezone.now()
        _event(visitor_id=scanner, subscriber=sub, when=now)
        _event(visitor_id=reader, subscriber=sub, when=now)
        _event(E.REVIEW_OPEN, visitor_id=reader, js_verified=True, when=now + datetime.timedelta(seconds=5))

        by_visitor = {v["visitor_id"]: v for v in visits._build_derived_visits(AnalyticsEvent.objects.all())}
        assert by_visitor[scanner]["deliberate"] is False
        assert by_visitor[reader]["deliberate"] is True

    def test_overview_counts_engaged_humans_and_share_attributed_visitors(self, analyst):
        sub = Subscriber.objects.create(email="ov@example.com")
        _event(visitor_id=uuid.uuid4(), subscriber=sub)  # scanner: not engaged
        sharer = uuid.uuid4()
        _event(visitor_id=sharer, share_token="tok123")
        _event(E.REVIEW_SHARE_EMAIL, visitor_id=sharer)
        _event(visitor_id=uuid.uuid4(), share_token="tok456")  # unfurl fetcher: not engaged

        response = analyst.get(reverse("backend:analytics_overview"))

        assert response.context["engaged_humans"] == 1
        assert response.context["share_attributed_visitors"] == 1


# ---- 2. Bot share ----


def test_bot_share_counts_each_request_once(analyst):
    today = timezone.localdate()
    for _ in range(3):
        _event()
    for _ in range(5):
        _event(automated=True)  # swept rows: already reflected in the counter
    AutomatedRequestCount.objects.create(date=today, event_type=E.PAGE_VISIT, reason="ua_marker", count=4)
    AutomatedRequestCount.objects.create(date=today, event_type=E.REVIEW_OPEN, reason="empty_ua", count=2)
    AutomatedRequestCount.objects.create(date=today, event_type=E.PAGE_VISIT, reason="observe_singleton", count=7)

    ctx = analyst.get(reverse("backend:analytics_overview")).context

    assert ctx["automated_count"] == 6
    assert ctx["total_attempted_events"] == 9  # 3 human + 6 filtered
    assert ctx["automated_share"] == "67%"
    assert sum(row["total"] for row in ctx["automated_reason_breakdown"]) == ctx["automated_count"]
    assert [row["reason"] for row in ctx["observed_reason_breakdown"]] == ["observe_singleton"]


# ---- 3 & 4. Traffic: engaged visit rate and local-date multi-day ----


def test_traffic_engaged_rate_uses_the_deliberate_definition(analyst):
    # A sustained view alone (gameable dwell) is no longer an engaged visit.
    dweller, clicker = uuid.uuid4(), uuid.uuid4()
    _event(E.REVIEW_ENGAGED, visitor_id=dweller, referrer_category="search", js_verified=True)
    _event(E.REVIEW_FULL_TEXT_CLICK, visitor_id=clicker, referrer_category="search")

    ctx = analyst.get(reverse("backend:analytics_traffic")).context

    search_row = next(row for row in ctx["referrer_breakdown"] if row["label"] == "Search engine")
    assert search_row["engaged_visits"] == 1
    assert search_row["engaged_rate"] == "50%"


def test_multi_day_uses_local_calendar_days(analyst):
    # 23:30 and 08:00 the next morning in Melbourne fall on the same UTC date.
    visitor = uuid.uuid4()
    day = timezone.localdate() - datetime.timedelta(days=3)
    late, early = _local(day, 23, 30), _local(day + datetime.timedelta(days=1), 8)
    _event(visitor_id=visitor, when=late)
    _event(visitor_id=visitor, when=early)
    assert late.astimezone(datetime.UTC).date() == early.astimezone(datetime.UTC).date()

    ctx = analyst.get(reverse("backend:analytics_traffic")).context

    assert ctx["multi_day_visitors"] == 1


# ---- 5. Default window ----


def test_default_window_is_the_90_day_preset(analyst):
    today = timezone.localdate()
    start_date, end_date, _, _ = common.default_analytics_window()
    assert (end_date - start_date).days + 1 == 90
    assert end_date == today

    ctx = analyst.get(reverse("backend:analytics_overview")).context
    active = [preset["label"] for preset in ctx["date_presets"] if preset["active"]]
    assert active == ["90d"]
    assert ctx["start_date"] == start_date


# ---- 6. Newsletter lift ----


def _newsletter(send_date):
    issue = Issue.objects.create(name=f"Issue {send_date.isoformat()}", body="body", active=True)
    return Newsletter.objects.create(issue=issue, subject="Lift", send_date=send_date, is_sent=True)


class TestNewsletterLift:
    def test_lift_is_none_while_the_after_week_runs(self):
        nl = _newsletter(timezone.now() - datetime.timedelta(days=2))
        _event(E.REVIEW_ENGAGED, when=nl.send_date - datetime.timedelta(days=1))
        rollout = timezone.localdate() - datetime.timedelta(days=60)

        row = overview._newsletter_lift_row(nl, rollout)

        assert row["in_progress"] is True
        assert row["lift_pct"] is None
        assert row["before"] == 1

    def test_lift_is_computed_on_local_days_once_the_after_week_ends(self):
        send_day = timezone.localdate() - datetime.timedelta(days=10)
        nl = _newsletter(_local(send_day, 9))
        _event(E.REVIEW_ENGAGED, when=_local(send_day - datetime.timedelta(days=7)))  # first instant of before-week
        _event(E.REVIEW_ENGAGED, when=_local(send_day - datetime.timedelta(days=8), 23, 59))  # outside
        _event(E.REVIEW_ENGAGED, when=_local(send_day, 1))  # send day, before the send: after-week
        _event(E.REVIEW_ENGAGED, when=_local(send_day, 12))
        _event(E.REVIEW_ENGAGED, when=_local(send_day + datetime.timedelta(days=7)))  # exclusive end

        row = overview._newsletter_lift_row(nl, send_day - datetime.timedelta(days=30))

        assert row["in_progress"] is False
        assert (row["before"], row["after"]) == (1, 2)
        assert row["lift_pct"] == 100

    def test_before_week_reaching_past_rollout_is_partial(self):
        send_day = timezone.localdate() - datetime.timedelta(days=10)
        nl = _newsletter(_local(send_day, 9))

        row = overview._newsletter_lift_row(nl, send_day - datetime.timedelta(days=3))

        assert row["site_analytics_partial"] is True
        assert row["lift_pct"] is None


# ---- 7. New vs returning ----


class TestReturningHistoryGuard:
    def _split(self, rollout_date):
        start_date = timezone.localdate() - datetime.timedelta(days=7)
        start_ts = _local(start_date)
        loyal = uuid.uuid4()
        _event(visitor_id=loyal, when=start_ts - datetime.timedelta(days=2))
        _event(visitor_id=loyal, when=start_ts + datetime.timedelta(days=1))
        return visits._split_new_returning(
            {loyal},
            start_ts=start_ts,
            start_date=start_date,
            rollout_date=rollout_date,
            visits_per_visitor={loyal: 1},
        )

    def test_history_needs_thirty_days_before_the_window(self):
        start_date = timezone.localdate() - datetime.timedelta(days=7)
        assert self._split(start_date - datetime.timedelta(days=29))[2] == "frequency"
        assert self._split(start_date - datetime.timedelta(days=30))[2] == "history"

    def test_rollout_is_the_first_visitor_id_event(self):
        first = timezone.now() - datetime.timedelta(days=40)
        _event(js_verified=True, when=first - datetime.timedelta(days=100))  # no visitor id: ignored
        _event(visitor_id=uuid.uuid4(), when=first)

        assert visits._visitor_id_rollout_date() == timezone.localtime(first).date()


# ---- 8. First-week benchmark ----


def _review(slug, publish_date):
    journal, _ = Journal.objects.get_or_create(name="Benchmark Journal")
    author, _ = Author.objects.get_or_create(name="Benchmark Author")
    article = PubmedArticle.objects.create(
        title=f"Article {slug}", journal=journal, citation="c", article_url="https://example.com", active=True
    )
    Review.objects.bulk_create(
        [Review(article=article, author=author, slug=slug, body="body", active=True, publish_date=publish_date)]
    )
    return Review.objects.get(slug=slug)


class TestFirstWeekBenchmark:
    def test_window_is_seven_days_with_an_exclusive_end(self):
        today = timezone.localdate()
        publish = today - datetime.timedelta(days=10)
        review = _review("bench-window", publish)
        review_ct = ContentType.objects.get_for_model(Review)
        for when in (
            _local(publish),
            _local(publish + datetime.timedelta(days=6), 23, 59),
            _local(publish + datetime.timedelta(days=7)),  # first instant after the window
        ):
            _event(E.REVIEW_OPEN, content_type=review_ct, object_id=review.pk, when=when)

        assert benchmarks._first_window_opens(review, review_ct, today) == 2

    def test_window_not_yet_elapsed_is_not_compared(self):
        today = timezone.localdate()
        review = _review("bench-fresh", today - datetime.timedelta(days=6))
        review_ct = ContentType.objects.get_for_model(Review)

        assert benchmarks._first_window_opens(review, review_ct, today) is None

    def test_cohort_starts_at_client_hints_enforcement(self):
        enforced = benchmarks.CLIENT_HINTS_ENFORCED_DATE
        today = enforced + datetime.timedelta(days=40)
        for i in range(5):
            _review(f"bench-after-{i}", enforced + datetime.timedelta(days=i))
        _review("bench-before", enforced - datetime.timedelta(days=1))
        review_ct = ContentType.objects.get_for_model(Review)

        _median, cohort = benchmarks._first_window_benchmark(review_ct, today)

        assert cohort == 5

    def test_blank_and_other_referrers_are_one_row(self):
        _event(referrer_category="")
        _event(referrer_category="other")
        _event(referrer_category="search")

        rows = benchmarks._referrer_source_breakdown(AnalyticsEvent.objects.all())

        assert [(row["label"], row["count"]) for row in rows] == [("Other", 2), ("Search engines", 1)]


# ---- 9. Period deltas ----


class TestPreviousPeriod:
    def test_range_ending_today_compares_the_same_elapsed_time(self, monkeypatch):
        now = _local(datetime.date(2026, 10, 22), 10, 30)
        _freeze(monkeypatch, now)
        start = datetime.date(2026, 10, 20)
        start_ts, _ = common._window_timestamps(start, start)

        prev_start, prev_end, prev_start_ts, prev_end_ts = common._previous_period(
            start, datetime.date(2026, 10, 22), start_ts
        )

        assert (prev_start, prev_end) == (datetime.date(2026, 10, 17), datetime.date(2026, 10, 19))
        assert prev_start_ts == _local(prev_start)
        assert prev_end_ts == _local(prev_end, 10)  # floored to the hour

    def test_past_range_compares_whole_days(self, monkeypatch):
        _freeze(monkeypatch, _local(datetime.date(2026, 10, 22), 10, 30))
        start, end = datetime.date(2026, 10, 1), datetime.date(2026, 10, 10)
        start_ts, _ = common._window_timestamps(start, end)

        _, prev_end, _, prev_end_ts = common._previous_period(start, end, start_ts)

        assert prev_end == datetime.date(2026, 9, 30)
        assert prev_end_ts == common._window_timestamps(prev_end, prev_end)[1]


# ---- 10. Send markers ----


def test_send_markers_use_the_local_send_date():
    send_day = timezone.localdate() - datetime.timedelta(days=5)
    _newsletter(_local(send_day, 8))  # 08:00 Melbourne is the previous evening in UTC

    assert common._newsletter_send_weeks()[0]["date"] == send_day.isoformat()


# ---- 11. Journals returning visitors ----


def test_journals_returning_ignores_automated_history(analyst):
    old = timezone.now() - datetime.timedelta(days=200)
    _event(visitor_id=uuid.uuid4(), when=old)  # dates the visitor_id rollout well before the window
    human_return, bot_history = uuid.uuid4(), uuid.uuid4()
    before_window = timezone.now() - datetime.timedelta(days=120)
    _event(E.JOURNAL_BROWSER_VISIT, visitor_id=human_return, when=before_window)
    _event(E.JOURNAL_BROWSER_VISIT, visitor_id=bot_history, automated=True, when=before_window)
    _event(E.JOURNAL_BROWSER_VISIT, visitor_id=human_return)
    _event(E.JOURNAL_BROWSER_VISIT, visitor_id=bot_history)

    ctx = analyst.get(reverse("backend:analytics_journals")).context

    assert ctx["returning_basis"] == "history"
    assert ctx["unique_visitors"] == 2
    assert ctx["returning_visitors"] == 1
