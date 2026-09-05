"""Derived visits: sessionising human events by visitor, landing pages, page sections and referrer buckets."""

import datetime
import hashlib
from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)

_PLACEHOLDER_SEARCH_QUERIES = frozenset(["{search_term_string}", "search_term_string"])
_LANDING_PAGE_EXACT_EXCLUSIONS = frozenset(
    ["/manifest.json", "/sw.js", "/robots.txt", "/healthz", "/site.webmanifest", "/favicon.ico"]
)
_LANDING_PAGE_SUFFIX_EXCLUSIONS = (".png", ".svg", ".xml", ".ico", ".js", ".json", ".webmanifest")
_LANDING_PAGE_PREFIX_EXCLUSIONS = ("/analytics/link/",)
_VISIT_INACTIVITY_GAP = datetime.timedelta(minutes=30)
_LOW_SAMPLE_THRESHOLD = 5
_VISIT_PAGE_PATHS = {
    "home": "/",
    "issue": "/issues",
    "tag": "/explore",
    "journals": "/journals",
    "search": "/search",
}
_PAGE_SECTION_LABELS = {
    "home": "Homepage",
    "issue": "Issue pages",
    "review": "Review pages",
    "tag": "Tag pages",
    "journals": "Journals browser",
    "search": "Search",
}
_JOURNAL_EVENT_TYPES = frozenset(
    [
        AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
        AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT,
        AnalyticsEvent.EventType.JOURNAL_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.JOURNAL_STAR,
        AnalyticsEvent.EventType.JOURNAL_RECOMMEND,
        AnalyticsEvent.EventType.JOURNAL_MARK_READ,
        AnalyticsEvent.EventType.JOURNAL_ARCHIVE,
        AnalyticsEvent.EventType.JOURNAL_SEARCH,
        AnalyticsEvent.EventType.JOURNAL_SELECT,
    ]
)
_VISIT_PROGRESSION_EVENT_TYPES = frozenset(
    [
        AnalyticsEvent.EventType.SEARCH_RESULT_CLICK,
        AnalyticsEvent.EventType.REVIEW_ENGAGED,
        AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
        AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT,
        AnalyticsEvent.EventType.JOURNAL_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.JOURNAL_STAR,
        AnalyticsEvent.EventType.JOURNAL_RECOMMEND,
        AnalyticsEvent.EventType.JOURNAL_MARK_READ,
        AnalyticsEvent.EventType.JOURNAL_ARCHIVE,
        AnalyticsEvent.EventType.JOURNAL_SELECT,
        AnalyticsEvent.EventType.NEWSLETTER_SUBSCRIBE,
    ]
)


def _split_new_returning(visitor_ids_in_period, *, start_ts, start_date, rollout_date, visits_per_visitor):
    """Split the period's visitors into (new_count, returning_count, basis).

    Returning is defined by whether a visitor was already seen *before* the
    period — the genuine loyalty signal, and the same definition the journals
    panel already uses. This replaces the older "2+ visits within the period",
    which counted a same-day repeat session as returning and labelled every
    single-visit visitor (including long-time readers) as "new".

    Seen-before is only meaningful once the jwvid cookie's history predates the
    period start. For windows reaching back to before analytics rollout there is
    no history to check, so we fall back to the in-period frequency heuristic and
    return basis="frequency" so the UI can describe what it's showing.
    """
    total = len(visitor_ids_in_period)
    history_reliable = rollout_date is not None and start_date > rollout_date
    if history_reliable and visitor_ids_in_period:
        returning_count = (
            AnalyticsEvent.objects.filter(
                automated=False,
                timestamp__lt=start_ts,
                visitor_id__in=visitor_ids_in_period,
            )
            .values("visitor_id")
            .distinct()
            .count()
        )
        basis = "history"
    else:
        returning_count = sum(1 for v in visits_per_visitor.values() if v >= 2)
        basis = "frequency"
    return total - returning_count, returning_count, basis


def _normalise_search_query(raw_query):
    query = (raw_query or "").strip()
    if not query:
        return "[browse]"
    if query.lower() in _PLACEHOLDER_SEARCH_QUERIES:
        return None
    return query


def _is_reportable_landing_page(path):
    cleaned_path = ((path or "").split("?", 1)[0]).strip()
    if not cleaned_path:
        return False
    if cleaned_path in _LANDING_PAGE_EXACT_EXCLUSIONS:
        return False
    if cleaned_path.startswith(_LANDING_PAGE_PREFIX_EXCLUSIONS):
        return False
    return not cleaned_path.endswith(_LANDING_PAGE_SUFFIX_EXCLUSIONS)


def _derive_visit_landing_page(row):
    landing_page = row.get("landing_page") or ""
    if _is_reportable_landing_page(landing_page):
        return landing_page

    metadata = row.get("metadata") or {}
    page = (metadata.get("page") or "").strip()
    if page in _VISIT_PAGE_PATHS:
        return _VISIT_PAGE_PATHS[page]

    event_type = row.get("event_type")
    if event_type in {
        AnalyticsEvent.EventType.SEARCH,
        AnalyticsEvent.EventType.SEARCH_RESULT_CLICK,
    }:
        return "/search"
    if event_type in {
        AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
        AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT,
        AnalyticsEvent.EventType.JOURNAL_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.JOURNAL_STAR,
        AnalyticsEvent.EventType.JOURNAL_RECOMMEND,
        AnalyticsEvent.EventType.JOURNAL_MARK_READ,
        AnalyticsEvent.EventType.JOURNAL_ARCHIVE,
        AnalyticsEvent.EventType.JOURNAL_SEARCH,
        AnalyticsEvent.EventType.JOURNAL_SELECT,
    }:
        return "/journals"
    return ""


def _derive_page_section(row):
    metadata = row.get("metadata") or {}
    page = (metadata.get("page") or "").strip()
    if page in _PAGE_SECTION_LABELS:
        return page

    event_type = row.get("event_type")
    if event_type in {
        AnalyticsEvent.EventType.REVIEW_OPEN,
        AnalyticsEvent.EventType.REVIEW_ENGAGED,
        AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    }:
        return "review"
    if event_type in {
        AnalyticsEvent.EventType.SEARCH,
        AnalyticsEvent.EventType.SEARCH_RESULT_CLICK,
    }:
        return "search"
    if event_type in _JOURNAL_EVENT_TYPES:
        return "journals"
    return ""


def _visit_partition_key(row):
    visitor_id = row.get("visitor_id")
    if visitor_id:
        return f"visitor:{visitor_id}"
    session_key = (row.get("session_key") or "").strip()
    if session_key:
        return f"session:{session_key}"
    return f"event:{row['id']}"


def _utm_field_from_metadata(row, key):
    metadata = row.get("metadata") or {}
    return (metadata.get(key) or "").strip()


def _build_derived_visits(events_qs):
    rows = list(
        events_qs.values(
            "id",
            "event_type",
            "timestamp",
            "visitor_id",
            "referrer_category",
            "referrer_domain",
            "landing_page",
            "metadata",
            "session_key",
            "js_verified",
            "human_confidence",
        )
    )
    # Group by visit_key then timestamp. A single visitor_id can span multiple
    # session_keys (e.g. when Django rotates the session cookie), so ordering
    # in SQL by (visitor_id, session_key, timestamp) would zigzag timestamps
    # within one visit and produce negative durations.
    rows.sort(key=lambda r: (_visit_partition_key(r), r["timestamp"], r["id"]))

    visits = []
    current_visit = None
    for row in rows:
        visit_key = _visit_partition_key(row)
        timestamp = row["timestamp"]
        should_start_new_visit = (
            current_visit is None
            or current_visit["visit_key"] != visit_key
            or timestamp - current_visit["last_event"] > _VISIT_INACTIVITY_GAP
        )
        if should_start_new_visit:
            current_visit = {
                "visit_key": visit_key,
                "visitor_id": row.get("visitor_id"),
                "referrer_category": row.get("referrer_category") or "",
                "referrer_domain": row.get("referrer_domain") or "",
                "landing_page": _derive_visit_landing_page(row),
                "first_event": timestamp,
                "last_event": timestamp,
                "js_verified": bool(row.get("js_verified")),
                "utm_source": _utm_field_from_metadata(row, "utm_source"),
                "utm_medium": _utm_field_from_metadata(row, "utm_medium"),
                "utm_campaign": _utm_field_from_metadata(row, "utm_campaign"),
                "events": [row],
            }
            visits.append(current_visit)
            continue

        current_visit["last_event"] = timestamp
        current_visit["events"].append(row)
        current_visit["js_verified"] = current_visit["js_verified"] or bool(row.get("js_verified"))
        if not current_visit["landing_page"]:
            current_visit["landing_page"] = _derive_visit_landing_page(row)
        if not current_visit["referrer_category"] and row.get("referrer_category"):
            current_visit["referrer_category"] = row["referrer_category"]
        if not current_visit["referrer_domain"] and row.get("referrer_domain"):
            current_visit["referrer_domain"] = row["referrer_domain"]
        if not current_visit["utm_source"]:
            current_visit["utm_source"] = _utm_field_from_metadata(row, "utm_source")
        if not current_visit["utm_medium"]:
            current_visit["utm_medium"] = _utm_field_from_metadata(row, "utm_medium")
        if not current_visit["utm_campaign"]:
            current_visit["utm_campaign"] = _utm_field_from_metadata(row, "utm_campaign")

    return visits


# Sessionising a 90-day window pulls ~17k rows into Python and spends ~1.7s
# building visits (the SQL itself is <0.4s). The dashboard panels each rebuild
# this from the same base queryset, so cache the result briefly and let every
# panel for a given date-range/scope share it. Analytics tolerate minutes of
# staleness; production uses Redis (a fresh copy per get, so no aliasing) and
# fails open, while local dev uses DummyCache (this is a transparent no-op).
_DERIVED_VISITS_CACHE_PREFIX = "analytics:derived_visits:"
_CACHE_MISS = object()


def _derived_visits_ttl():
    return int(getattr(settings, "ANALYTICS_DERIVED_VISITS_CACHE_TTL", 600))


def _build_derived_visits_cached(events_qs):
    """Cached wrapper around :func:`_build_derived_visits`.

    Keyed on the queryset's SQL, so identical scope + date-range across panels
    (and repeat navigations) reuse one build. Falls back to an uncached build if
    the query can't be rendered to a stable key.
    """
    ttl = _derived_visits_ttl()
    if ttl <= 0:
        return _build_derived_visits(events_qs)
    try:
        signature = str(events_qs.query)
    except Exception:  # noqa: BLE001 — never let key derivation break the view
        return _build_derived_visits(events_qs)
    key = _DERIVED_VISITS_CACHE_PREFIX + hashlib.md5(signature.encode("utf-8")).hexdigest()  # noqa: S324
    cached = cache.get(key, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached
    visits = _build_derived_visits(events_qs)
    cache.set(key, visits, ttl)
    return visits


def _weekly_visits_by_referrer(visits, categories, weeks=26):
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    non_other = frozenset(c for c in categories if c != "other")

    # Single pass: bucket visits by week-start date and tally per-category counts.
    week_cat_counts: dict = {}
    for visit in visits:
        visit_date = visit["first_event"].astimezone(tz).date()
        ws = visit_date - datetime.timedelta(days=visit_date.weekday())
        cat = visit["referrer_category"] or ""
        if ws not in week_cat_counts:
            week_cat_counts[ws] = Counter()
        week_cat_counts[ws][cat] += 1

    labels = []
    series = {cat: [] for cat in categories}
    for i in range(weeks - 1, -1, -1):
        week_start = today - datetime.timedelta(days=today.weekday() + 7 * i)
        labels.append(week_start.strftime("%-d %b"))
        cat_counts = week_cat_counts.get(week_start, {})
        for cat in categories:
            if cat == "other":
                count = sum(v for k, v in cat_counts.items() if k not in non_other)
            else:
                count = cat_counts.get(cat, 0)
            series[cat].append(count)
    return labels, series


def _weekly_visit_buckets(visits, weeks=26):
    today = timezone.localdate()
    tz = timezone.get_current_timezone()

    week_counts: Counter = Counter()
    for visit in visits:
        visit_date = visit["first_event"].astimezone(tz).date()
        ws = visit_date - datetime.timedelta(days=visit_date.weekday())
        week_counts[ws] += 1

    return [
        {
            "label": (ws := today - datetime.timedelta(days=today.weekday() + 7 * i)).strftime("%-d %b"),
            "count": week_counts.get(ws, 0),
            "week_start": ws.isoformat(),
        }
        for i in range(weeks - 1, -1, -1)
    ]


def _rank_rows(rows, sort_fields, limit=8):
    def _sort_key(item):
        values = []
        for field in sort_fields:
            value = item.get(field)
            values.append(0 if value is None else value)
        return tuple(values)

    return sorted(rows, key=_sort_key, reverse=True)[:limit]
