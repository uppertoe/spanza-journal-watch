import datetime
import uuid
from collections import Counter

import pytest
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.backend.analytics_views import _split_new_returning

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
