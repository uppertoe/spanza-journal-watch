import datetime
import uuid
from collections import Counter

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.backend.analytics_views import (
    _build_derived_visits_cached,
    _engaged_human_count,
    _split_new_returning,
)
from spanza_journal_watch.newsletter.models import Subscriber

pytestmark = pytest.mark.django_db


def _aware(dt):
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _event(visitor_id, when, *, automated=False):
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=automated,
        visitor_id=visitor_id,
    )
    AnalyticsEvent.objects.filter(pk=event.pk).update(timestamp=_aware(when))
    return event


def _window():
    start_date = timezone.localdate() - datetime.timedelta(days=7)
    start_ts = _aware(datetime.datetime.combine(start_date, datetime.time.min))
    rollout_date = start_date - datetime.timedelta(days=30)  # history predates the window
    return start_date, start_ts, rollout_date


def test_history_basis_splits_on_seen_before_period():
    start_date, start_ts, rollout_date = _window()
    seen_before = uuid.uuid4()
    first_timer = uuid.uuid4()
    _event(seen_before, start_ts - datetime.timedelta(days=2))  # prior history → returning
    _event(seen_before, start_ts + datetime.timedelta(days=1))
    _event(first_timer, start_ts + datetime.timedelta(days=1))  # only in-period → new

    new_count, returning_count, basis = _split_new_returning(
        {seen_before, first_timer},
        start_ts=start_ts,
        start_date=start_date,
        rollout_date=rollout_date,
        visits_per_visitor=Counter(),
    )

    assert basis == "history"
    assert returning_count == 1
    assert new_count == 1


def test_history_basis_ignores_automated_prior_events():
    start_date, start_ts, rollout_date = _window()
    spoofed = uuid.uuid4()
    _event(spoofed, start_ts - datetime.timedelta(days=2), automated=True)  # bot history doesn't count
    _event(spoofed, start_ts + datetime.timedelta(days=1))

    new_count, returning_count, basis = _split_new_returning(
        {spoofed},
        start_ts=start_ts,
        start_date=start_date,
        rollout_date=rollout_date,
        visits_per_visitor=Counter(),
    )

    assert basis == "history"
    assert returning_count == 0
    assert new_count == 1


def test_falls_back_to_frequency_when_window_predates_rollout():
    start_date, start_ts, _ = _window()
    rollout_date = start_date + datetime.timedelta(days=1)  # window starts before rollout → no history
    loyal = uuid.uuid4()
    once = uuid.uuid4()

    new_count, returning_count, basis = _split_new_returning(
        {loyal, once},
        start_ts=start_ts,
        start_date=start_date,
        rollout_date=rollout_date,
        visits_per_visitor=Counter({loyal: 2, once: 1}),  # 2+ visits = returning
    )

    assert basis == "frequency"
    assert returning_count == 1
    assert new_count == 1


def test_falls_back_to_frequency_when_no_rollout_date():
    start_date, start_ts, _ = _window()
    new_count, returning_count, basis = _split_new_returning(
        {uuid.uuid4()},
        start_ts=start_ts,
        start_date=start_date,
        rollout_date=None,
        visits_per_visitor=Counter(),
    )

    assert basis == "frequency"
    assert returning_count == 0
    assert new_count == 1


# ---- _build_derived_visits_cached ----


def test_derived_visits_cached_serves_warm_call_without_queries(settings):
    settings.ANALYTICS_DERIVED_VISITS_CACHE_TTL = 600
    cache.clear()
    _event(uuid.uuid4(), timezone.now())
    qs = AnalyticsEvent.objects.filter(automated=False)

    with CaptureQueriesContext(connection) as cold:
        first = _build_derived_visits_cached(qs)
    with CaptureQueriesContext(connection) as warm:
        second = _build_derived_visits_cached(qs)

    assert len(cold.captured_queries) >= 1  # cold build hits the DB
    assert len(warm.captured_queries) == 0  # warm call served from cache
    assert first == second
    assert len(first) == 1


def test_derived_visits_cache_keyed_by_queryset_scope(settings):
    settings.ANALYTICS_DERIVED_VISITS_CACHE_TTL = 600
    cache.clear()
    _event(uuid.uuid4(), timezone.now())
    _event(uuid.uuid4(), timezone.now(), automated=True)

    humans = _build_derived_visits_cached(AnalyticsEvent.objects.filter(automated=False))
    everyone = _build_derived_visits_cached(AnalyticsEvent.objects.all())

    # Different scope → different cache key → not cross-contaminated.
    assert len(humans) == 1
    assert len(everyone) == 2


# ---- _engaged_human_count ----


def _engaged_event(visitor_id, *, event_type=AnalyticsEvent.EventType.PAGE_VISIT, scroll_depth=None, subscriber=None):
    return AnalyticsEvent.objects.create(
        event_type=event_type,
        automated=False,
        visitor_id=visitor_id,
        scroll_depth=scroll_depth,
        subscriber=subscriber,
    )


def test_engaged_human_count_counts_only_hard_interactions():
    qs = AnalyticsEvent.objects.all()
    scroller = uuid.uuid4()
    searcher = uuid.uuid4()
    dweller = uuid.uuid4()
    clicker = uuid.uuid4()
    _engaged_event(scroller, scroll_depth=95)  # deep scroll only → gamed, NOT engaged
    _engaged_event(searcher, event_type=AnalyticsEvent.EventType.SEARCH)  # bare search → gamed, NOT engaged
    _engaged_event(dweller, event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)  # 5s dwell → gamed, NOT engaged
    _engaged_event(clicker, event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)  # hard click → engaged

    assert _engaged_human_count(qs) == 1


def test_engaged_human_count_includes_subscriber_and_excludes_bots():
    qs = AnalyticsEvent.objects.filter(automated=False)
    sub = Subscriber.objects.create(email="reader@example.com")
    subscriber_visitor = uuid.uuid4()
    bot_visitor = uuid.uuid4()
    _engaged_event(subscriber_visitor, subscriber=sub)  # matched subscriber → engaged
    AnalyticsEvent.objects.create(  # automated, deep scroll, but excluded by qs filter
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=True,
        visitor_id=bot_visitor,
        scroll_depth=95,
    )

    assert _engaged_human_count(qs) == 1


def test_derived_visits_cache_disabled_with_zero_ttl(settings):
    settings.ANALYTICS_DERIVED_VISITS_CACHE_TTL = 0
    cache.clear()
    _event(uuid.uuid4(), timezone.now())
    qs = AnalyticsEvent.objects.filter(automated=False)

    _build_derived_visits_cached(qs)
    with CaptureQueriesContext(connection) as warm:
        _build_derived_visits_cached(qs)

    assert len(warm.captured_queries) >= 1  # no caching → still hits the DB
