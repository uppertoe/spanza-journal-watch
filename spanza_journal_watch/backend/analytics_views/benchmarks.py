"""First-week open benchmarks and confidence notes for reviews."""

import datetime

from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
)
from spanza_journal_watch.submissions.models import Review

from .common import _engaged_human_count
from .visits import _VISIT_PROGRESSION_EVENT_TYPES, _derive_page_section

# ── Recency / freshness helpers ────────────────────────────────────
# Absolute-count leaderboards structurally favour older reviews: a review live
# for 90 days has had 90 days to bank opens, one released 3 days ago has had 3.
# These helpers let the dashboard surface just-released content on its own terms
# (velocity, and opens-in-first-week vs the median for the cohort).

_FIRST_WINDOW_DAYS = 7
_BENCHMARK_LOOKBACK_DAYS = 180
_BENCHMARK_MIN_COHORT = 5


def _review_publish_date(review):
    """Best-effort publication date: review's own date, else its earliest issue."""
    if review.publish_date:
        return review.publish_date
    issue_dates = [i.date for i in review.issues.all() if i.date]
    return min(issue_dates) if issue_dates else None


def _first_window_opens(review, review_ct, today, days=_FIRST_WINDOW_DAYS):
    """REVIEW_OPEN count within the review's own first ``days`` after publish.

    Bounded per review and independent of the dashboard window — a brand-new
    review's first week may sit outside the selected range. Callers pass a small
    set (reviews published recently), so the per-review count stays cheap.
    """
    publish = _review_publish_date(review)
    if publish is None:
        return None
    window_end = publish + datetime.timedelta(days=days)
    # Only meaningful once the full first window has elapsed.
    if window_end > today:
        return None
    start_ts = timezone.make_aware(datetime.datetime.combine(publish, datetime.time.min))
    end_ts = timezone.make_aware(datetime.datetime.combine(window_end, datetime.time.max))
    return AnalyticsEvent.objects.filter(
        content_type=review_ct,
        object_id=review.pk,
        event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
        timestamp__gte=start_ts,
        timestamp__lte=end_ts,
        automated=False,
    ).count()


def _first_window_benchmark(review_ct, today):
    """Median first-week REVIEW_OPEN count across recently-published reviews.

    Cached briefly — it walks a bounded cohort (reviews published in the trailing
    ~180 days whose first window has fully elapsed). Returns (median, cohort_size);
    median is None until the cohort reaches _BENCHMARK_MIN_COHORT.
    """
    cache_key = f"analytics_first_week_benchmark:{today.isoformat()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["median"], cached["cohort"]

    cohort_start = today - datetime.timedelta(days=_BENCHMARK_LOOKBACK_DAYS)
    window_cutoff = today - datetime.timedelta(days=_FIRST_WINDOW_DAYS)
    counts = []
    reviews = (
        Review.objects.filter(active=True, publish_date__gte=cohort_start, publish_date__lte=window_cutoff)
        .prefetch_related("issues")
        .only("id", "publish_date")
    )
    for review in reviews:
        opens = _first_window_opens(review, review_ct, today)
        if opens is not None:
            counts.append(opens)

    median = None
    if len(counts) >= _BENCHMARK_MIN_COHORT:
        counts.sort()
        mid = len(counts) // 2
        median = counts[mid] if len(counts) % 2 else (counts[mid - 1] + counts[mid]) / 2

    result = {"median": median, "cohort": len(counts)}
    cache.set(cache_key, result, 600)
    return median, len(counts)


_REFERRER_CATEGORY_LABELS = {
    "newsletter": "Newsletter",
    "search": "Search engines",
    "social": "Social",
    "internal": "Internal (on-site)",
    "direct": "Direct / bookmark",
    "other": "Other",
}


def _referrer_source_breakdown(events_qs):
    """How readers arrived — grouped by referrer_category, ordered by count.

    Returns a list of {label, category, count, pct}. ``pct`` is the share of the
    total, so the template can render proportional bars without a second pass.
    """
    rows = list(events_qs.values("referrer_category").annotate(count=Count("id")).order_by("-count"))
    total = sum(r["count"] for r in rows)
    breakdown = []
    for r in rows:
        category = r["referrer_category"] or "other"
        breakdown.append(
            {
                "label": _REFERRER_CATEGORY_LABELS.get(category, category.title() if category else "Other"),
                "category": category,
                "count": r["count"],
                "pct": round(r["count"] / total * 100) if total else 0,
            }
        )
    return breakdown


def _benchmark_verdict(first_week_opens, median):
    """Classify a review's first-week opens against the cohort median."""
    if first_week_opens is None or median is None or median <= 0:
        return None
    ratio = first_week_opens / median
    if ratio >= 1.25:
        return "outperforming"
    if ratio <= 0.75:
        return "underperforming"
    return "on_track"


def _is_one_step_visit(visit):
    sections_seen = {section for section in (_derive_page_section(row) for row in visit["events"]) if section}
    progressed = any(row["event_type"] in _VISIT_PROGRESSION_EVENT_TYPES for row in visit["events"])
    return len(sections_seen) <= 1 and not progressed


def _confidence_summary(events_qs):
    """Return confidence metrics for a queryset of AnalyticsEvent."""
    total = events_qs.count()
    if not total:
        return {
            "conf_total": 0,
            "conf_js_rate": "—",
            "conf_subscriber_rate": "—",
            "conf_engaged_humans": 0,
        }
    js = events_qs.filter(js_verified=True).count()
    subs = events_qs.filter(human_confidence="known_subscriber_human").count()
    return {
        "conf_total": total,
        "conf_js_rate": _safe_percentage(js, total),
        "conf_subscriber_rate": _safe_percentage(subs, total),
        "conf_engaged_humans": _engaged_human_count(events_qs),
    }
