"""The warming task leaves the dashboards' derived visits in the cache."""

import pytest
from django.core.cache import cache
from django.test import override_settings

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.backend.analytics_views import visits
from spanza_journal_watch.backend.analytics_views.common import default_analytics_window, human_events_between

pytestmark = pytest.mark.django_db

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _event(event_type, **fields):
    return AnalyticsEvent.objects.create(event_type=event_type, automated=False, **fields)


class TestWarmDerivedVisits:
    def test_request_path_reuses_the_warmed_build(self, settings, monkeypatch):
        settings.ANALYTICS_DERIVED_VISITS_CACHE_TTL = 900
        _event(AnalyticsEvent.EventType.REVIEW_OPEN, metadata={"page": "review"})
        _event(AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT, metadata={"page": "journals"})
        with override_settings(CACHES=LOCMEM):
            cache.clear()
            counts = visits.warm_derived_visits()
            assert counts["all"] >= 1
            assert counts["journals"] >= 1

            def explode(_qs):
                raise AssertionError("the request path rebuilt visits after warming")

            monkeypatch.setattr(visits, "_build_derived_visits", explode)
            _, _, start_ts, end_ts = default_analytics_window()
            human_events = human_events_between(start_ts, end_ts)
            assert len(visits._build_derived_visits_cached(human_events)) == counts["all"]
            journal_events = human_events.filter(event_type__in=visits._journal_event_type_list())
            assert len(visits._build_derived_visits_cached(journal_events)) == counts["journals"]

    def test_force_overwrites_a_stale_copy(self, settings):
        settings.ANALYTICS_DERIVED_VISITS_CACHE_TTL = 900
        with override_settings(CACHES=LOCMEM):
            cache.clear()
            _, _, start_ts, end_ts = default_analytics_window()
            qs = human_events_between(start_ts, end_ts)
            assert visits._build_derived_visits_cached(qs) == []
            _event(AnalyticsEvent.EventType.REVIEW_OPEN, metadata={"page": "review"})
            assert visits._build_derived_visits_cached(qs) == []  # still the cached, empty copy
            assert len(visits._build_derived_visits_cached(qs, force=True)) == 1
