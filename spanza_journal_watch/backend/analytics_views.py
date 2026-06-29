"""
Analytics views for the editorial backend.

Five tabs, each answering a specific question:
  Overview – How is the site performing?
  Editorial Intelligence – What should we cover next?
  Audience – Who reads and how do they find us?
  Newsletter Impact – Is the newsletter driving engagement?
  Feature Adoption – Are our features being used?
"""

import datetime
import hashlib
import json
from collections import Counter, defaultdict

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
    AutomatedRequestCount,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.backend.views import (
    _build_rate_row,
    _newsletter_predates_site_analytics,
    _parse_iso_date,
    _safe_percentage,
    _site_analytics_rollout_date,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Review


def _pct_change(current, previous):
    """Return percentage change as an integer, or None if no previous data."""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


VIEW_SITE_ANALYTICS = "backend.view_site_analytics"
VIEW_NEWSLETTER_STATS = "backend.view_newsletter_stats"

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


# "Engaged human" KPI: a distinct visitor counts only once they take a
# *deliberate* action (DELIBERATE_INTERACTION_EVENT_TYPES) or match a subscriber.
# Passive scroll and bare `search` submissions are excluded — a 2026-06 prod
# audit showed JS-executing bots game both. The same constant gates the
# UA-cohort bot sweeper, so "engaged" and "not swept as a bot" always agree.
def _engaged_human_count(events_qs):
    """Distinct visitors in ``events_qs`` who took a deliberate engagement action.

    ``events_qs`` is expected to already be human-filtered + period-scoped
    (i.e. ``_base_event_qs``). Counts distinct ``visitor_id``s that either fired
    a deliberate interaction or matched a subscriber.
    """
    return (
        events_qs.filter(visitor_id__isnull=False)
        .filter(Q(subscriber__isnull=False) | Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES))
        .values("visitor_id")
        .distinct()
        .count()
    )


def _date_range_from_request(request, default_days=90):
    today = timezone.localdate()
    default_start = today - datetime.timedelta(days=default_days)
    start_date = _parse_iso_date(request.GET.get("start")) or default_start
    end_date = _parse_iso_date(request.GET.get("end")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    start_ts = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
    end_ts = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))
    return start_date, end_date, start_ts, end_ts


def _base_event_qs(request, start_ts, end_ts):
    """Return AnalyticsEvent queryset filtered by date, always excluding automated rows."""
    return AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)


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


def _render_analytics(request, template, context, panel_template=None):
    start_date = context.get("start_date")
    end_date = context.get("end_date")
    if start_date and end_date:
        today = timezone.localdate()
        presets = []
        for days in (30, 90, 180):
            preset_start = today - datetime.timedelta(days=days - 1)
            presets.append(
                {
                    "label": f"{days}d",
                    "start": preset_start.isoformat(),
                    "end": today.isoformat(),
                    "active": start_date == preset_start and end_date == today,
                }
            )
        context.setdefault("date_presets", presets)
    if request.headers.get("HX-Request") and panel_template:
        return render(request, panel_template, context)
    return render(request, template, context)


def _weekly_buckets(qs, timestamp_field="timestamp", weeks=26):
    from django.db.models.functions import TruncWeek

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    cutoff_date = today - datetime.timedelta(days=today.weekday() + 7 * (weeks - 1))
    cutoff_dt = timezone.make_aware(datetime.datetime.combine(cutoff_date, datetime.time.min))

    raw = {
        entry["week"].astimezone(tz).date(): entry["count"]
        for entry in qs.filter(**{f"{timestamp_field}__gte": cutoff_dt})
        .annotate(week=TruncWeek(timestamp_field, tzinfo=tz))
        .values("week")
        .annotate(count=Count("id"))
    }

    return [
        {
            "label": (week_start := today - datetime.timedelta(days=today.weekday() + 7 * i)).strftime("%-d %b"),
            "count": raw.get(week_start, 0),
            "week_start": week_start.isoformat(),
        }
        for i in range(weeks - 1, -1, -1)
    ]


def _newsletter_send_weeks(weeks=26):
    today = timezone.localdate()
    cutoff = today - datetime.timedelta(weeks=weeks)
    newsletters = (
        Newsletter.objects.filter(
            is_sent=True,
            send_date__date__gte=cutoff,
        )
        .order_by("send_date")
        .values("send_date", "id")
    )
    return [{"date": n["send_date"].date().isoformat(), "id": n["id"]} for n in newsletters]


_FLOW_LABELS = {
    "review_open": "Impression",
    "review_engaged": "Sustained view",
    "review_full_text_click": "Full text",
    "review_related_click": "Related click",
    "review_share_copy_link": "Share (copy)",
    "review_share_email": "Share (email)",
    "review_share_native": "Share (native)",
    "review_share_bluesky": "Share (Bluesky)",
    "review_share_x": "Share (X)",
    "review_share_facebook": "Share (Facebook)",
    "search": "Search",
    "search_result_click": "Search click",
    "page_visit": "Page visit",
    "journal_browser_visit": "Journals browser",
    "journal_article_interact": "Journal article",
    "journal_select": "Journal selected",
    "journal_star": "Journal starred",
    "journal_recommend": "Journal recommended",
    "journal_mark_read": "Journal marked read",
    "journal_archive": "Journal archived",
    "journal_search": "Journal search",
    "journal_full_text_click": "Journal full text",
    "newsletter_subscribe": "Newsletter subscribe",
    "cpd_tracking_toggle": "CPD tracking toggle",
}


_ENGAGED_VISIT_EVENT_TYPES = {
    AnalyticsEvent.EventType.REVIEW_ENGAGED,
    AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
}
_ENGAGED_VISIT_MIN_DURATION_S = 30


def _visit_is_engaged(visit):
    if any(row["event_type"] in _ENGAGED_VISIT_EVENT_TYPES for row in visit["events"]):
        return True
    duration = (visit["last_event"] - visit["first_event"]).total_seconds()
    return duration >= _ENGAGED_VISIT_MIN_DURATION_S


def _resolve_content_titles(event_ids):
    """Bulk-resolve content_object labels for the given event ids.

    Returns {event_id: "label"}. Labels fall back to the object's __str__.
    """
    if not event_ids:
        return {}
    rows = list(AnalyticsEvent.objects.filter(id__in=event_ids).values("id", "content_type_id", "object_id"))
    # Group event ids by (content_type_id, object_id) to load each object once.
    ids_by_ct = defaultdict(set)
    for row in rows:
        if row["content_type_id"] and row["object_id"]:
            ids_by_ct[row["content_type_id"]].add(row["object_id"])

    titles = {}
    for ct_id, obj_ids in ids_by_ct.items():
        try:
            ct = ContentType.objects.get_for_id(ct_id)
            model = ct.model_class()
        except (ContentType.DoesNotExist, LookupError):
            continue
        if model is None:
            continue
        try:
            for obj_id, obj in model._default_manager.in_bulk(obj_ids).items():
                titles[(ct_id, obj_id)] = str(obj)
        except Exception:
            continue

    result = {}
    for row in rows:
        key = (row["content_type_id"], row["object_id"])
        if key in titles:
            result[row["id"]] = titles[key]
    return result


def _enrich_event_details(event_rows):
    """Fetch duration_ms / scroll_depth / share_token + content titles for event rows."""
    if not event_rows:
        return {}, {}
    ids = [row["id"] for row in event_rows]
    extras = {
        row["id"]: row
        for row in AnalyticsEvent.objects.filter(id__in=ids).values("id", "duration_ms", "scroll_depth", "share_token")
    }
    titles = _resolve_content_titles(ids)
    # Enrich from metadata foreign-key hints that aren't captured via
    # content_object (e.g. journal_select stores journal_id in metadata, not
    # as a GenericForeignKey).
    journal_ids = set()
    article_ids = set()
    for row in event_rows:
        if row["id"] in titles:
            continue
        metadata = row.get("metadata") or {}
        jid = metadata.get("journal_id")
        aid = metadata.get("article_id")
        if isinstance(jid, int) and jid > 0:
            journal_ids.add(jid)
        if isinstance(aid, int) and aid > 0:
            article_ids.add(aid)
    journal_labels = {}
    if journal_ids:
        from spanza_journal_watch.submissions.models import Journal

        journal_labels = {j.pk: str(j) for j in Journal.objects.filter(pk__in=journal_ids)}
    article_labels = {}
    if article_ids:
        from spanza_journal_watch.backend.models import PubmedArticle

        article_labels = {a.pk: a.get_title() for a in PubmedArticle.objects.filter(pk__in=article_ids)}
    for row in event_rows:
        if row["id"] in titles:
            continue
        metadata = row.get("metadata") or {}
        jid = metadata.get("journal_id")
        aid = metadata.get("article_id")
        if isinstance(jid, int) and jid in journal_labels:
            titles[row["id"]] = journal_labels[jid]
        elif isinstance(aid, int) and aid in article_labels:
            titles[row["id"]] = article_labels[aid]
    return extras, titles


def _format_event_detail(event_row, extra, title):
    parts = []
    if title:
        parts.append(title)
    duration_ms = extra.get("duration_ms") if extra else None
    if duration_ms:
        parts.append(f"{duration_ms / 1000:.1f}s")
    scroll = extra.get("scroll_depth") if extra else None
    if scroll is not None and scroll > 0:
        parts.append(f"scroll {scroll}%")
    share_token = (extra or {}).get("share_token")
    if share_token:
        parts.append(f"share {share_token[:8]}")
    metadata = event_row.get("metadata") or {}
    query = metadata.get("query")
    if query:
        parts.append(str(query))
    elif not title:
        path = metadata.get("path") or metadata.get("destination_url")
        if path:
            parts.append(str(path))
        else:
            page = metadata.get("page")
            if page:
                parts.append(_VISIT_PAGE_PATHS.get(page, str(page)))
    return " · ".join(parts)


def _compute_top_flows(visits):
    """Return the top two-step transitions across derived visits.

    Self-transitions (X → X) are dropped — they're usually page_visit / scroll
    flush noise and crowd out genuinely informative movement between surfaces.
    """
    transition_counter = Counter()
    total_visits_with_transition = 0
    for visit in visits:
        event_types = [row["event_type"] for row in visit["events"][:10]]
        seen = set()
        had_transition = False
        for i in range(len(event_types) - 1):
            if event_types[i] == event_types[i + 1]:
                continue
            pair = (event_types[i], event_types[i + 1])
            if pair not in seen:
                transition_counter[pair] += 1
                seen.add(pair)
                had_transition = True
        if had_transition:
            total_visits_with_transition += 1

    return [
        {
            "from": _FLOW_LABELS.get(a, a),
            "to": _FLOW_LABELS.get(b, b),
            "count": count,
            "pct": _safe_percentage(count, total_visits_with_transition),
        }
        for (a, b), count in transition_counter.most_common(12)
    ]


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_overview(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_ct)

    share_event_types = [
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    ]

    E = AnalyticsEvent.EventType
    period_agg = review_events.aggregate(
        total_opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
        total_engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
        total_full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
        total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        avg_dwell=Avg("duration_ms", filter=Q(event_type=E.REVIEW_ENGAGED)),
        avg_scroll=Avg("scroll_depth", filter=Q(event_type=E.REVIEW_ENGAGED, scroll_depth__isnull=False)),
    )
    total_opens = period_agg["total_opens"]
    total_engaged = period_agg["total_engaged"]
    total_full_text = period_agg["total_full_text"]
    total_shares = period_agg["total_shares"]
    avg_dwell_ms = period_agg["avg_dwell"] or 0
    avg_scroll = period_agg["avg_scroll"]
    avg_scroll_depth = round(avg_scroll) if avg_scroll is not None else None

    search_count = (
        human_events.filter(event_type=E.SEARCH)
        .exclude(metadata__query="")
        .exclude(metadata__query__isnull=True)
        .count()
    )

    # Previous period for comparison
    period_days = (end_date - start_date).days
    prev_end = start_date - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=period_days)
    prev_start_ts = timezone.make_aware(datetime.datetime.combine(prev_start, datetime.time.min))
    prev_end_ts = timezone.make_aware(datetime.datetime.combine(prev_end, datetime.time.max))

    # The comparison is only meaningful if the previous period sits wholly within
    # the analytics era; otherwise it baselines against near-zero data and every
    # delta explodes (e.g. +5936%). Suppress the deltas in that case.
    _rollout_date = _site_analytics_rollout_date()
    comparison_reliable = _rollout_date is not None and prev_start >= _rollout_date

    def _delta(current, previous):
        return _pct_change(current, previous) if comparison_reliable else None

    prev_human = AnalyticsEvent.objects.filter(
        timestamp__gte=prev_start_ts, timestamp__lte=prev_end_ts, automated=False
    )
    prev_review = prev_human.filter(content_type=review_ct)
    prev_agg = prev_review.aggregate(
        total_opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
        total_engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
        total_full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
        total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        avg_dwell=Avg("duration_ms", filter=Q(event_type=E.REVIEW_ENGAGED)),
        avg_scroll=Avg("scroll_depth", filter=Q(event_type=E.REVIEW_ENGAGED, scroll_depth__isnull=False)),
    )
    prev_opens = prev_agg["total_opens"]
    prev_engaged = prev_agg["total_engaged"]
    prev_full_text = prev_agg["total_full_text"]
    prev_shares = prev_agg["total_shares"]
    prev_dwell_ms = prev_agg["avg_dwell"] or 0
    prev_scroll = prev_agg["avg_scroll"]
    prev_scroll_depth = round(prev_scroll) if prev_scroll is not None else None
    prev_searches = (
        prev_human.filter(event_type=E.SEARCH)
        .exclude(metadata__query="")
        .exclude(metadata__query__isnull=True)
        .count()
    )

    engaged_qs = AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED, automated=False)
    weekly_trend = _weekly_buckets(engaged_qs)
    newsletter_sends = _newsletter_send_weeks(weeks=26)

    recent_newsletters = list(Newsletter.objects.filter(is_sent=True).order_by("-send_date")[:4])
    newsletter_lift = []
    for nl in recent_newsletters:
        if not nl.send_date:
            continue
        if _newsletter_predates_site_analytics(nl):
            newsletter_lift.append(
                {
                    "newsletter": nl,
                    "before": None,
                    "after": None,
                    "lift_pct": None,
                    "site_analytics_partial": True,
                }
            )
            continue
        send_dt = nl.send_date
        before_start = send_dt - datetime.timedelta(days=7)
        after_end = send_dt + datetime.timedelta(days=7)
        lift_qs = AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED, automated=False)
        before_count = lift_qs.filter(timestamp__gte=before_start, timestamp__lt=send_dt).count()
        after_count = lift_qs.filter(timestamp__gte=send_dt, timestamp__lte=after_end).count()
        lift_pct = None
        if before_count:
            lift_pct = round((after_count - before_count) / before_count * 100)
        newsletter_lift.append(
            {
                "newsletter": nl,
                "before": before_count,
                "after": after_count,
                "lift_pct": lift_pct,
                "site_analytics_partial": False,
            }
        )

    visits = _build_derived_visits_cached(human_events)

    # Unique visitors and visits
    unique_visitors = len({v["visitor_id"] for v in visits if v["visitor_id"]})
    engaged_humans = _engaged_human_count(human_events)
    unique_sessions = len(visits)
    # Distinct Django session_keys (only written when a request mutates the
    # session, e.g. a JS beacon fires). Used as the bot-signal denominator:
    # cookie-only crawlers rarely trigger session writes, so visitor_id /
    # session_key climbs when they slip through the UA filter.
    unique_session_keys = human_events.exclude(session_key="").values("session_key").distinct().count()

    human_agg = human_events.aggregate(
        human_event_count=Count("id"),
        subscriber_events=Count(
            "id", filter=Q(human_confidence=AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN)
        ),
    )
    human_event_count = human_agg["human_event_count"]
    subscriber_events = human_agg["subscriber_events"]

    # Data quality — always across ALL events regardless of filter toggle
    all_period = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts)
    all_agg = all_period.aggregate(
        total_all_events=Count("id"),
        js_verified_count=Count("id", filter=Q(js_verified=True)),
    )
    total_all_events = all_agg["total_all_events"]
    # Automated requests are no longer persisted per-row; read from the daily
    # aggregate counter bumped by record_event's bot short-circuit.
    automated_counter_qs = AutomatedRequestCount.objects.filter(
        date__gte=start_ts.date(),
        date__lte=end_ts.date(),
    )
    automated_count = automated_counter_qs.aggregate(total=Coalesce(Sum("count"), 0))["total"]
    automated_breakdown = list(
        automated_counter_qs.values("event_type").annotate(total=Sum("count")).order_by("-total")
    )
    automated_reason_breakdown = list(
        automated_counter_qs.values("reason").annotate(total=Sum("count")).order_by("-total")
    )
    total_attempted_events = total_all_events + automated_count
    js_verified_count = all_agg["js_verified_count"]
    confidence_breakdown = list(all_period.values("human_confidence").annotate(count=Count("id")).order_by("-count"))

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
            total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        )
        .order_by("-opens", "-engaged_views", "-full_text_clicks")
    )
    review_ids = [row["object_id"] for row in review_summary_rows if row["object_id"]]
    reviews_by_id = {
        review.id: review
        for review in Review.objects.filter(id__in=review_ids)
        .select_related("article__journal")
        .defer("body", "search_vector", "article__abstract", "article__metadata_json", "article__tags_string")
    }
    review_rows = []
    for row in review_summary_rows:
        review = reviews_by_id.get(row["object_id"])
        if not review:
            continue
        review_rows.append(
            {
                "review": review,
                "opens": row["opens"],
                "engaged_views": row["engaged_views"],
                "engaged_rate": _safe_percentage(row["engaged_views"], row["opens"]),
                "engaged_rate_value": (row["engaged_views"] / row["opens"]) if row["opens"] else 0,
                "full_text_clicks": row["full_text_clicks"],
                "full_text_ctr": _safe_percentage(row["full_text_clicks"], row["opens"]),
                "full_text_ctr_value": (row["full_text_clicks"] / row["opens"]) if row["opens"] else 0,
                "total_shares": row["total_shares"],
                "share_rate": _safe_percentage(row["total_shares"], row["opens"]),
                "share_rate_value": (row["total_shares"] / row["opens"]) if row["opens"] else 0,
                "low_sample": row["opens"] < _LOW_SAMPLE_THRESHOLD,
            }
        )

    best_opened_review = _rank_rows(review_rows, ("opens", "engaged_views", "full_text_clicks"), limit=1)
    best_full_text_review = _rank_rows(review_rows, ("full_text_clicks", "full_text_ctr_value", "opens"), limit=1)
    most_shared_review = _rank_rows(review_rows, ("total_shares", "share_rate_value", "opens"), limit=1)

    share_count_map = dict(
        review_events.filter(event_type__in=share_event_types)
        .values_list("event_type")
        .annotate(count=Count("id"))
        .values_list("event_type", "count")
    )
    share_counts = {
        "Copy link": share_count_map.get(E.REVIEW_SHARE_COPY_LINK, 0),
        "Email": share_count_map.get(E.REVIEW_SHARE_EMAIL, 0),
        "Native share": share_count_map.get(E.REVIEW_SHARE_NATIVE, 0),
        "Bluesky": share_count_map.get(E.REVIEW_SHARE_BLUESKY, 0),
        "X": share_count_map.get(E.REVIEW_SHARE_X, 0),
        "Facebook": share_count_map.get(E.REVIEW_SHARE_FACEBOOK, 0),
    }
    top_share_method = None
    for label, count in sorted(share_counts.items(), key=lambda item: item[1], reverse=True):
        if count:
            top_share_method = {"label": label, "count": count}
            break
    # Visitors who arrived via a share link (carry a ref token) AND actually
    # engaged. Excludes link-preview/unfurl fetchers, which land a single
    # tokened page_visit with no interaction and would otherwise inflate this.
    share_attributed_visits = (
        human_events.exclude(share_token="")
        .filter(visitor_id__isnull=False)
        .filter(Q(subscriber__isnull=False) | Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES))
        .values("visitor_id")
        .distinct()
        .count()
    )

    search_query_counter = Counter()
    zero_result_counter = Counter()
    search_click_counter = Counter()
    for metadata in human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH).values_list("metadata", flat=True):
        query = _normalise_search_query((metadata or {}).get("query"))
        if query in {None, "[browse]"}:
            continue
        search_query_counter[query] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[query] += 1
    for metadata in human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK).values_list(
        "metadata", flat=True
    ):
        query = _normalise_search_query((metadata or {}).get("query"))
        if query in {None, "[browse]"}:
            continue
        search_click_counter[query] += 1

    top_dead_end_query = None
    search_dead_end_rows = sorted(
        [
            {
                "query": query,
                "searches": count,
                "result_clicks": search_click_counter.get(query, 0),
                "zero_results": zero_result_counter.get(query, 0),
            }
            for query, count in search_query_counter.items()
            if search_click_counter.get(query, 0) == 0 or zero_result_counter.get(query, 0) > 0
        ],
        key=lambda item: (item["searches"], item["zero_results"], -item["result_clicks"]),
        reverse=True,
    )
    if search_dead_end_rows:
        top_dead_end_query = search_dead_end_rows[0]

    referrer_labels = {
        "newsletter": "Newsletter",
        "search": "Search engine",
        "social": "Social media",
        "direct": "Direct",
        "internal": "Internal",
        "other": "Other",
        "": "Unknown",
    }
    landing_summary = defaultdict(lambda: {"visits": 0, "engaged_visits": 0, "one_step_visits": 0})
    section_summary = defaultdict(lambda: {"visits": 0, "engaged_visits": 0, "one_step_visits": 0})
    source_summary = defaultdict(int)
    for visit in visits:
        engaged_visit = any(row["event_type"] == AnalyticsEvent.EventType.REVIEW_ENGAGED for row in visit["events"])
        one_step_visit = _is_one_step_visit(visit)
        source_summary[visit["referrer_category"] or ""] += 1
        if visit["landing_page"]:
            landing = landing_summary[visit["landing_page"]]
            landing["visits"] += 1
            if engaged_visit:
                landing["engaged_visits"] += 1
            if one_step_visit:
                landing["one_step_visits"] += 1
        sections_seen = {section for section in (_derive_page_section(row) for row in visit["events"]) if section}
        for section in sections_seen:
            summary = section_summary[section]
            summary["visits"] += 1
            if engaged_visit:
                summary["engaged_visits"] += 1
            if one_step_visit:
                summary["one_step_visits"] += 1

    landing_focus = None
    landing_candidates = []
    for path, summary in landing_summary.items():
        if not summary["visits"]:
            continue
        one_step_rate_value = summary["one_step_visits"] / summary["visits"]
        landing_candidates.append(
            {
                "path": path,
                "visits": summary["visits"],
                "engaged_rate": _safe_percentage(summary["engaged_visits"], summary["visits"]),
                "one_step_rate": _safe_percentage(summary["one_step_visits"], summary["visits"]),
                "one_step_rate_value": one_step_rate_value,
                "low_sample": summary["visits"] < _LOW_SAMPLE_THRESHOLD,
            }
        )
    if landing_candidates:
        landing_focus = sorted(
            landing_candidates,
            key=lambda item: (item["one_step_rate_value"], item["visits"]),
            reverse=True,
        )[0]

    section_focus = None
    section_candidates = []
    for section, summary in section_summary.items():
        if not summary["visits"]:
            continue
        one_step_rate_value = summary["one_step_visits"] / summary["visits"]
        section_candidates.append(
            {
                "label": _PAGE_SECTION_LABELS.get(section, section),
                "visits": summary["visits"],
                "engaged_rate": _safe_percentage(summary["engaged_visits"], summary["visits"]),
                "one_step_rate": _safe_percentage(summary["one_step_visits"], summary["visits"]),
                "one_step_rate_value": one_step_rate_value,
                "low_sample": summary["visits"] < _LOW_SAMPLE_THRESHOLD,
            }
        )
    if section_candidates:
        section_focus = sorted(
            section_candidates,
            key=lambda item: (item["one_step_rate_value"], item["visits"]),
            reverse=True,
        )[0]

    top_source = None
    source_candidates = [
        {"label": referrer_labels.get(category, category or "Unknown"), "visits": count}
        for category, count in source_summary.items()
        if count
    ]
    if source_candidates:
        top_source = sorted(source_candidates, key=lambda item: item["visits"], reverse=True)[0]

    overview_editorial_items = []
    if best_opened_review:
        item = best_opened_review[0]
        overview_editorial_items.append(
            {
                "title": "Most opened review",
                "detail": (
                    f"{item['review'].article.get_title()} drew {item['opens']} opens "
                    f"with {item['engaged_rate']} engaged."
                ),
                "tone": "primary",
            }
        )
    if best_full_text_review and best_full_text_review[0]["full_text_clicks"]:
        item = best_full_text_review[0]
        overview_editorial_items.append(
            {
                "title": "Strongest click-through",
                "detail": (
                    f"{item['review'].article.get_title()} drove {item['full_text_clicks']} "
                    f"full-text click{'s' if item['full_text_clicks'] != 1 else ''} "
                    f"({item['full_text_ctr']})."
                ),
                "tone": "success",
            }
        )
    elif most_shared_review and most_shared_review[0]["total_shares"]:
        item = most_shared_review[0]
        overview_editorial_items.append(
            {
                "title": "Most shared review",
                "detail": (
                    f"{item['review'].article.get_title()} was shared {item['total_shares']} "
                    f"time{'s' if item['total_shares'] != 1 else ''} ({item['share_rate']})."
                ),
                "tone": "info",
            }
        )
    if top_share_method:
        share_detail = (
            f"{top_share_method['label']} was the main share path with {top_share_method['count']} "
            f"share action{'s' if top_share_method['count'] != 1 else ''}."
        )
        if share_attributed_visits:
            share_detail += (
                f" {share_attributed_visits} visit{'s' if share_attributed_visits != 1 else ''} "
                f"came back through share links."
            )
        overview_editorial_items.append(
            {
                "title": "How readers shared",
                "detail": share_detail,
                "tone": "info",
            }
        )

    overview_dev_items = []
    if landing_focus:
        detail = (
            f"{landing_focus['path']} saw {landing_focus['visits']} "
            f"visit{'s' if landing_focus['visits'] != 1 else ''}, "
            f"with {landing_focus['one_step_rate']} ending there and "
            f"{landing_focus['engaged_rate']} reaching engaged reading."
        )
        if landing_focus["low_sample"]:
            detail += " Low sample."
        overview_dev_items.append(
            {
                "title": "Landing friction",
                "detail": detail,
                "tone": "warning",
            }
        )
    if top_dead_end_query:
        detail = (
            f"'{top_dead_end_query['query']}' was searched {top_dead_end_query['searches']} time"
            f"{'s' if top_dead_end_query['searches'] != 1 else ''}"
        )
        if top_dead_end_query["zero_results"]:
            detail += (
                f", with {top_dead_end_query['zero_results']} "
                f"zero-result search{'es' if top_dead_end_query['zero_results'] != 1 else ''}"
            )
        if not top_dead_end_query["result_clicks"]:
            detail += ", and no result clicks"
        detail += "."
        overview_dev_items.append(
            {
                "title": "Search friction",
                "detail": detail,
                "tone": "warning",
            }
        )
    if section_focus:
        detail = (
            f"{section_focus['label']} appeared in {section_focus['visits']} "
            f"visit{'s' if section_focus['visits'] != 1 else ''}; "
            f"{section_focus['one_step_rate']} stopped there and "
            f"{section_focus['engaged_rate']} led to engaged reading."
        )
        if section_focus["low_sample"]:
            detail += " Low sample."
        overview_dev_items.append(
            {
                "title": "Section to watch",
                "detail": detail,
                "tone": "secondary",
            }
        )

    overview_confidence_items = [
        {
            "title": "Traffic scope",
            "detail": (
                "Human uses the bot filter as a best estimate. "
                "All traffic includes crawler, prefetch, and other noisy events."
            ),
            "tone": "secondary",
        }
    ]
    if unique_sessions < 20:
        overview_confidence_items.append(
            {
                "title": "Low-volume caution",
                "detail": (
                    f"This range only contains {unique_sessions} "
                    f"derived visit{'s' if unique_sessions != 1 else ''}, "
                    "so treat week-on-week changes as directional."
                ),
                "tone": "warning",
            }
        )
    if any(item["site_analytics_partial"] for item in newsletter_lift):
        overview_confidence_items.append(
            {
                "title": "Partial newsletter attribution",
                "detail": (
                    "Some newsletter sends predate the current site analytics rollout, "
                    "so click-through and post-send traffic should be read cautiously."
                ),
                "tone": "warning",
            }
        )
    elif top_source:
        overview_confidence_items.append(
            {
                "title": "Main acquisition source",
                "detail": f"{top_source['label']} accounts for the largest share of visit starts in this range.",
                "tone": "info",
            }
        )

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "unique_visitors": unique_visitors,
        "engaged_humans": engaged_humans,
        "unique_sessions": unique_sessions,
        "total_opens": total_opens,
        "total_engaged": total_engaged,
        "engaged_rate": _safe_percentage(total_engaged, total_opens),
        "average_dwell_seconds": round(avg_dwell_ms / 1000, 1),
        "total_full_text": total_full_text,
        "full_text_ctr": _safe_percentage(total_full_text, total_opens),
        "total_shares": total_shares,
        "share_rate": _safe_percentage(total_shares, total_opens),
        "search_count": search_count,
        "avg_scroll_depth": avg_scroll_depth,
        "weekly_trend": weekly_trend,
        "newsletter_sends": newsletter_sends,
        "newsletter_lift": newsletter_lift,
        "human_event_count": human_event_count,
        "subscriber_events": subscriber_events,
        "subscriber_share": _safe_percentage(subscriber_events, human_event_count),
        "total_all_events": total_all_events,
        "automated_count": automated_count,
        "total_attempted_events": total_attempted_events,
        "automated_share": _safe_percentage(automated_count, total_attempted_events),
        "automated_breakdown": automated_breakdown,
        "automated_reason_breakdown": automated_reason_breakdown,
        "unique_session_keys": unique_session_keys,
        "visitor_session_ratio": (round(unique_visitors / unique_session_keys, 1) if unique_session_keys else None),
        "js_verified_count": js_verified_count,
        "confidence_breakdown": confidence_breakdown,
        "delta_opens": _delta(total_opens, prev_opens),
        "delta_engaged": _delta(total_engaged, prev_engaged),
        "delta_dwell": _delta(avg_dwell_ms, prev_dwell_ms),
        "delta_full_text": _delta(total_full_text, prev_full_text),
        "delta_shares": _delta(total_shares, prev_shares),
        "delta_searches": _delta(search_count, prev_searches),
        "delta_scroll": (_delta(avg_scroll_depth or 0, prev_scroll_depth or 0) if avg_scroll_depth else None),
        "comparison_label": f"vs {prev_start.strftime('%-d %b')} – {prev_end.strftime('%-d %b')}",
        "comparison_reliable": comparison_reliable,
        "overview_editorial_items": overview_editorial_items,
        "overview_dev_items": overview_dev_items,
        "overview_confidence_items": overview_confidence_items,
        "active_tab": "overview",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(
        request, "backend/analytics/overview.html", context, "backend/analytics/_overview_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_content(request):
    """Legacy URL — redirects to editorial."""
    qs = request.GET.urlencode()
    url = reverse("backend:analytics_editorial")
    return redirect(f"{url}?{qs}" if qs else url)


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_editorial(request):
    """Editorial Intelligence — merges content engagement + search data."""
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_ct)

    E = AnalyticsEvent.EventType
    share_event_types = [
        E.REVIEW_SHARE_COPY_LINK,
        E.REVIEW_SHARE_EMAIL,
        E.REVIEW_SHARE_NATIVE,
        E.REVIEW_SHARE_BLUESKY,
        E.REVIEW_SHARE_X,
        E.REVIEW_SHARE_FACEBOOK,
    ]
    editorial_agg = review_events.aggregate(
        total_opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
        total_engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
        total_full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
        total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
    )
    total_opens = editorial_agg["total_opens"]
    total_engaged = editorial_agg["total_engaged"]
    total_full_text = editorial_agg["total_full_text"]
    total_shares = editorial_agg["total_shares"]

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            avg_dwell_ms=Avg("duration_ms", filter=Q(event_type=E.REVIEW_ENGAGED)),
            avg_scroll=Avg("scroll_depth", filter=Q(event_type=E.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
            total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        )
        .order_by("-opens", "-engaged_views", "-full_text_clicks")
    )

    review_ids = [row["object_id"] for row in review_summary_rows if row["object_id"]]
    reviews_by_id = {
        r.id: r
        for r in Review.objects.filter(id__in=review_ids)
        .select_related("article__journal", "author")
        .prefetch_related("article__tags")
        .defer("body", "search_vector", "article__abstract", "article__metadata_json", "article__tags_string")
    }

    top_reviews = []
    journal_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})
    tag_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})

    for row in review_summary_rows:
        review = reviews_by_id.get(row["object_id"])
        if not review:
            continue
        score = row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
        engaged_rate_value = (row["engaged_views"] / row["opens"]) if row["opens"] else 0
        share_rate_value = (row["total_shares"] / row["opens"]) if row["opens"] else 0
        full_text_ctr_value = (row["full_text_clicks"] / row["opens"]) if row["opens"] else 0
        top_reviews.append(
            {
                "review": review,
                "opens": row["opens"],
                "engaged_views": row["engaged_views"],
                "avg_dwell_seconds": round((row["avg_dwell_ms"] or 0) / 1000, 1),
                "full_text_clicks": row["full_text_clicks"],
                "total_shares": row["total_shares"],
                "engagement_score": score,
                "engaged_rate": _safe_percentage(row["engaged_views"], row["opens"]),
                "share_rate": _safe_percentage(row["total_shares"], row["opens"]),
                "full_text_ctr": _safe_percentage(row["full_text_clicks"], row["opens"]),
                "engaged_rate_value": engaged_rate_value,
                "share_rate_value": share_rate_value,
                "full_text_ctr_value": full_text_ctr_value,
                "avg_scroll_depth": round(row["avg_scroll"]) if row["avg_scroll"] is not None else None,
                "low_sample": row["opens"] < _LOW_SAMPLE_THRESHOLD,
            }
        )
        journal = review.article.journal.name if review.article and review.article.journal else "Unknown"
        journal_totals[journal]["opens"] += row["opens"]
        journal_totals[journal]["engaged"] += row["engaged_views"]
        journal_totals[journal]["shares"] += row["total_shares"]
        journal_totals[journal]["full_text"] += row["full_text_clicks"]
        journal_totals[journal]["score"] += score
        for tag in review.article.tags.all():
            label = str(tag)
            tag_totals[label]["opens"] += row["opens"]
            tag_totals[label]["engaged"] += row["engaged_views"]
            tag_totals[label]["shares"] += row["total_shares"]
            tag_totals[label]["full_text"] += row["full_text_clicks"]
            tag_totals[label]["score"] += score

    # Engagement velocity — score per week since publication
    today = timezone.localdate()
    for item in top_reviews:
        review = item["review"]
        if review.publish_date:
            days_since = max((today - review.publish_date).days, 1)
            weeks_since = max(days_since / 7, 0.5)
            item["velocity"] = round(item["engagement_score"] / weeks_since, 1)
            item["weeks_since"] = round(weeks_since, 1)
        else:
            item["velocity"] = None
            item["weeks_since"] = None

    top_reviews_by_reach = _rank_rows(top_reviews, ("opens", "engaged_views", "full_text_clicks"))
    top_reviews_by_depth = _rank_rows(top_reviews, ("engaged_views", "avg_dwell_seconds", "avg_scroll_depth"))
    top_reviews_by_full_text = _rank_rows(top_reviews, ("full_text_clicks", "full_text_ctr_value", "opens"))
    top_reviews_by_shares = _rank_rows(top_reviews, ("total_shares", "share_rate_value", "opens"))
    top_journals = sorted(
        ({"label": k, **v} for k, v in journal_totals.items()),
        key=lambda x: x["score"],
        reverse=True,
    )[:10]
    top_tags = sorted(
        ({"label": k, **v} for k, v in tag_totals.items()),
        key=lambda x: x["score"],
        reverse=True,
    )[:15]

    featured_ids = {r.id for r in reviews_by_id.values() if r.is_featured}
    featured_rows = [row for row in review_summary_rows if row["object_id"] in featured_ids]
    standard_rows = [row for row in review_summary_rows if row["object_id"] not in featured_ids]

    def _group_summary(label, rows):
        opens = sum(r["opens"] for r in rows)
        engaged = sum(r["engaged_views"] for r in rows)
        shares = sum(r["total_shares"] for r in rows)
        full_text = sum(r["full_text_clicks"] for r in rows)
        score = sum(
            r["opens"] + r["engaged_views"] * 3 + r["full_text_clicks"] * 4 + r["total_shares"] * 5 for r in rows
        )
        return _build_rate_row(
            label=label, opens=opens, engaged=engaged, shares=shares, full_text=full_text, score=score
        )

    editorial_share_map = dict(
        review_events.filter(event_type__in=share_event_types)
        .values_list("event_type")
        .annotate(count=Count("id"))
        .values_list("event_type", "count")
    )
    share_counts = {
        "Copy link": editorial_share_map.get(E.REVIEW_SHARE_COPY_LINK, 0),
        "Email": editorial_share_map.get(E.REVIEW_SHARE_EMAIL, 0),
        "Native share": editorial_share_map.get(E.REVIEW_SHARE_NATIVE, 0),
        "Bluesky": editorial_share_map.get(E.REVIEW_SHARE_BLUESKY, 0),
        "X": editorial_share_map.get(E.REVIEW_SHARE_X, 0),
        "Facebook": editorial_share_map.get(E.REVIEW_SHARE_FACEBOOK, 0),
    }
    share_breakdown = [{"label": k, "count": v} for k, v in share_counts.items() if v]

    # Share-to-visit attribution — count downstream visits from share tokens
    # Visitors who arrived via a share link (carry a ref token) AND actually
    # engaged. Excludes link-preview/unfurl fetchers, which land a single
    # tokened page_visit with no interaction and would otherwise inflate this.
    share_attributed_visits = (
        human_events.exclude(share_token="")
        .filter(visitor_id__isnull=False)
        .filter(Q(subscriber__isnull=False) | Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES))
        .values("visitor_id")
        .distinct()
        .count()
    )

    # Tag-based content type breakdown — shows what topics resonate
    tag_type_breakdown = sorted(
        (
            {
                "label": k,
                "opens": v["opens"],
                "engaged": v["engaged"],
                "engaged_rate": _safe_percentage(v["engaged"], v["opens"]),
                "full_text": v["full_text"],
                "full_text_ctr": _safe_percentage(v["full_text"], v["opens"]),
                "score": v["score"],
            }
            for k, v in tag_totals.items()
        ),
        key=lambda x: x["score"],
        reverse=True,
    )[:10]

    # ── Search data (merged from analytics_search) ──────────────────
    search_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
    search_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK)

    search_query_counter = Counter()
    zero_result_counter = Counter()
    for metadata in search_events.values_list("metadata", flat=True):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label is None:
            continue
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1

    click_counter = Counter()
    for metadata in search_click_events.values_list("metadata", flat=True):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label is None:
            continue
        click_counter[label] += 1

    browse_searches = search_query_counter.pop("[browse]", 0)
    browse_clicks = click_counter.pop("[browse]", 0)
    zero_result_counter.pop("[browse]", 0)

    real_search_total = sum(search_query_counter.values())
    real_click_total = sum(click_counter.values())

    search_insights = [
        {
            "label": label,
            "searches": count,
            "result_clicks": click_counter.get(label, 0),
            "click_through_rate": _safe_percentage(click_counter.get(label, 0), count),
            "zero_results": zero_result_counter.get(label, 0),
        }
        for label, count in search_query_counter.most_common(20)
    ]

    zero_result_queries = sorted(
        [item for item in search_insights if item["zero_results"] > 0],
        key=lambda x: -x["zero_results"],
    )

    weekly_searches = _weekly_buckets(search_events)

    # ── Cross-reference: topics with unmet demand ───────────────────
    # Match zero-result search terms against tag names (case-insensitive)
    tag_names_lower = {}
    for label in tag_totals:
        tag_names_lower[label.lower()] = label
        tag_names_lower[label.lower().lstrip("#")] = label
    unmet_demand = []
    for zq in zero_result_queries[:10]:
        q_lower = zq["label"].lower()
        matched_tag = tag_names_lower.get(q_lower)
        if matched_tag:
            tag_data = tag_totals[matched_tag]
            unmet_demand.append(
                {
                    "query": zq["label"],
                    "searches": zq["searches"],
                    "zero_results": zq["zero_results"],
                    "tag_score": tag_data["score"],
                }
            )

    # ── Archive discovery via "Related reviews" clicks ──────────────────
    related_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_RELATED_CLICK)
    related_clicks_total = related_click_events.count()
    related_by_surface = Counter()
    for source in related_click_events.values_list("source", flat=True):
        related_by_surface[source or "unknown"] += 1
    _SURFACE_LABELS = {
        "review_detail": "Review page",
        "card_modal": "Home/card modal",
        "issue": "Issue page",
        "journal_browser": "Journal browser",
        "unknown": "Unknown",
    }
    related_surface_breakdown = sorted(
        ({"label": _SURFACE_LABELS.get(s, s), "count": c} for s, c in related_by_surface.items()),
        key=lambda x: -x["count"],
    )
    # Top archive destinations reached via a Related click.
    related_dest_rows = list(
        related_click_events.filter(content_type=review_ct)
        .values("object_id")
        .annotate(clicks=Count("id"))
        .order_by("-clicks")[:10]
    )
    related_dest_ids = [r["object_id"] for r in related_dest_rows if r["object_id"]]
    related_dest_reviews = {
        r.id: r for r in Review.objects.filter(id__in=related_dest_ids).select_related("article__journal")
    }
    top_related_destinations = [
        {"review": related_dest_reviews[r["object_id"]], "clicks": r["clicks"]}
        for r in related_dest_rows
        if r["object_id"] in related_dest_reviews
    ]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        # Content engagement
        "total_opens": total_opens,
        "total_engaged": total_engaged,
        "total_full_text": total_full_text,
        "total_shares": total_shares,
        "engaged_rate": _safe_percentage(total_engaged, total_opens),
        "full_text_ctr": _safe_percentage(total_full_text, total_opens),
        "share_rate": _safe_percentage(total_shares, total_opens),
        "top_reviews_by_reach": top_reviews_by_reach,
        "top_reviews_by_depth": top_reviews_by_depth,
        "top_reviews_by_full_text": top_reviews_by_full_text,
        "top_reviews_by_shares": top_reviews_by_shares,
        "top_journals": top_journals,
        "top_tags": top_tags,
        "tag_type_breakdown": tag_type_breakdown,
        "review_type_breakdown": [
            _group_summary("Featured reviews", featured_rows),
            _group_summary("Standard reviews", standard_rows),
        ],
        "share_breakdown": share_breakdown,
        "share_attributed_visits": share_attributed_visits,
        "share_attributed_visit_rate": _safe_percentage(share_attributed_visits, total_shares),
        # Search data
        "total_searches": real_search_total,
        "search_click_count": real_click_total,
        "search_ctr": _safe_percentage(real_click_total, real_search_total),
        "browse_searches": browse_searches,
        "browse_ctr": _safe_percentage(browse_clicks, browse_searches),
        "search_insights": search_insights,
        "zero_result_queries": zero_result_queries[:10],
        "weekly_searches": weekly_searches,
        "unmet_demand": unmet_demand,
        # Archive discovery
        "related_clicks_total": related_clicks_total,
        "related_surface_breakdown": related_surface_breakdown,
        "top_related_destinations": top_related_destinations,
        "active_tab": "content",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(
        request, "backend/analytics/content.html", context, "backend/analytics/_content_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_traffic(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)
    human_events = _base_event_qs(request, start_ts, end_ts)
    visits = _build_derived_visits_cached(human_events)

    referrer_labels = {
        "newsletter": "Newsletter",
        "search": "Search engine",
        "social": "Social media",
        "direct": "Direct",
        "internal": "Internal",
        "other": "Other",
        "": "Unknown",
    }
    visit_source_summary = defaultdict(
        lambda: {
            "visits": 0,
            "visitor_ids": set(),
            "engaged_visits": 0,
            "js_verified_visits": 0,
        }
    )
    for visit in visits:
        category = visit["referrer_category"] or ""
        summary = visit_source_summary[category]
        summary["visits"] += 1
        if visit["visitor_id"]:
            summary["visitor_ids"].add(visit["visitor_id"])
        if any(row["event_type"] == AnalyticsEvent.EventType.REVIEW_ENGAGED for row in visit["events"]):
            summary["engaged_visits"] += 1
        if visit["js_verified"]:
            summary["js_verified_visits"] += 1

    referrer_breakdown = sorted(
        [
            {
                "label": referrer_labels.get(category, category or "Unknown"),
                "visits": summary["visits"],
                "visitors": len(summary["visitor_ids"]),
                "engaged_visits": summary["engaged_visits"],
                "engaged_rate": _safe_percentage(summary["engaged_visits"], summary["visits"]),
                "js_verified": summary["js_verified_visits"],
                "js_rate": _safe_percentage(summary["js_verified_visits"], summary["visits"]),
            }
            for category, summary in visit_source_summary.items()
        ],
        key=lambda row: row["visits"],
        reverse=True,
    )

    # Top referrer domains for first-touch "Other" visits
    other_domain_counts = Counter()
    for visit in visits:
        if visit["referrer_category"] == "other" and visit["referrer_domain"]:
            other_domain_counts[visit["referrer_domain"]] += 1
    other_domains = [
        {"referrer_domain": domain, "count": count} for domain, count in other_domain_counts.most_common(10)
    ]

    visitor_ids_in_period = {visit["visitor_id"] for visit in visits if visit["visitor_id"]}

    # Per-visitor visit frequency and distinct active days within the period.
    visits_per_visitor = Counter()
    visitor_dates = defaultdict(set)
    for visit in visits:
        vid = visit["visitor_id"]
        if not vid:
            continue
        visits_per_visitor[vid] += 1
        visitor_dates[vid].add(visit["first_event"].date())
    multi_day_count = sum(1 for ds in visitor_dates.values() if len(ds) >= 2)

    rollout_date = _site_analytics_rollout_date()
    new_count, returning_count, returning_basis = _split_new_returning(
        visitor_ids_in_period,
        start_ts=start_ts,
        start_date=start_date,
        rollout_date=rollout_date,
        visits_per_visitor=visits_per_visitor,
    )

    page_counts = Counter()
    section_summary = defaultdict(lambda: {"visits": 0, "engaged_visits": 0, "single_event_visits": 0})
    landing_summary = defaultdict(lambda: {"visits": 0, "engaged_visits": 0, "single_event_visits": 0})
    for visit in visits:
        engaged_visit = any(row["event_type"] == AnalyticsEvent.EventType.REVIEW_ENGAGED for row in visit["events"])
        single_event_visit = _is_one_step_visit(visit)
        sections_seen = {section for section in (_derive_page_section(row) for row in visit["events"]) if section}
        for section in sections_seen:
            page_counts[section] += 1
            summary = section_summary[section]
            summary["visits"] += 1
            if engaged_visit:
                summary["engaged_visits"] += 1
            if single_event_visit:
                summary["single_event_visits"] += 1
        if visit["landing_page"]:
            summary = landing_summary[visit["landing_page"]]
            summary["visits"] += 1
            if engaged_visit:
                summary["engaged_visits"] += 1
            if single_event_visit:
                summary["single_event_visits"] += 1
    page_breakdown = [
        {
            "label": _PAGE_SECTION_LABELS.get(page, page),
            "visits": section_summary[page]["visits"],
            "engaged_rate": _safe_percentage(section_summary[page]["engaged_visits"], section_summary[page]["visits"]),
            "one_step_rate": _safe_percentage(
                section_summary[page]["single_event_visits"], section_summary[page]["visits"]
            ),
            "low_sample": section_summary[page]["visits"] < _LOW_SAMPLE_THRESHOLD,
        }
        for page, _count in page_counts.most_common()
    ]

    journal_visits = sum(
        1 for visit in visits if any(row["event_type"] in _JOURNAL_EVENT_TYPES for row in visit["events"])
    )

    traffic_categories = ["newsletter", "search", "social", "direct", "other"]
    traffic_chart_labels, traffic_chart_series = _weekly_visits_by_referrer(visits, categories=traffic_categories)

    # Landing page distribution — first page in each derived visit
    landing_counts = Counter()
    for visit in visits:
        if visit["landing_page"]:
            landing_counts[visit["landing_page"]] += 1
    landing_breakdown = [
        {
            "path": path,
            "visits": landing_summary[path]["visits"],
            "engaged_rate": _safe_percentage(landing_summary[path]["engaged_visits"], landing_summary[path]["visits"]),
            "one_step_rate": _safe_percentage(
                landing_summary[path]["single_event_visits"], landing_summary[path]["visits"]
            ),
            "low_sample": landing_summary[path]["visits"] < _LOW_SAMPLE_THRESHOLD,
        }
        for path, _count in landing_counts.most_common(10)
    ]

    # UTM campaign breakdown
    campaign_counts = Counter()
    for visit in visits:
        source = visit["utm_source"]
        medium = visit["utm_medium"]
        campaign = visit["utm_campaign"]
        if source:
            label = source
            if medium:
                label += f" / {medium}"
            if campaign:
                label += f" / {campaign}"
            campaign_counts[label] += 1
    campaign_breakdown = [{"label": label, "visits": count} for label, count in campaign_counts.most_common(10)]

    search_query_counter = Counter()
    zero_result_counter = Counter()
    click_counter = Counter()
    for metadata in human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH).values_list("metadata", flat=True):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label in {None, "[browse]"}:
            continue
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1
    for metadata in human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK).values_list(
        "metadata", flat=True
    ):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label in {None, "[browse]"}:
            continue
        click_counter[label] += 1
    search_dead_ends = sorted(
        [
            {
                "query": label,
                "searches": count,
                "result_clicks": click_counter.get(label, 0),
                "zero_results": zero_result_counter.get(label, 0),
                "low_sample": count < _LOW_SAMPLE_THRESHOLD,
            }
            for label, count in search_query_counter.items()
            if click_counter.get(label, 0) == 0 or zero_result_counter.get(label, 0) > 0
        ],
        key=lambda item: (item["searches"], item["zero_results"], -item["result_clicks"]),
        reverse=True,
    )[:8]

    # Top visit flows — most common 2-step transitions
    flow_counts = _compute_top_flows(visits)

    # Recent visit explorer
    engaged_only = request.GET.get("engaged_only") in ("1", "true", "yes")
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    page_size = 50

    sorted_visits = sorted(visits, key=lambda visit: visit["last_event"], reverse=True)
    if engaged_only:
        filtered_visits = [v for v in sorted_visits if _visit_is_engaged(v)]
    else:
        filtered_visits = sorted_visits
    recent_session_total = len(filtered_visits)
    total_pages = max(1, (recent_session_total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start_idx = (page - 1) * page_size
    recent_visit_rows = filtered_visits[start_idx : start_idx + page_size]

    recent_sessions = []
    for visit in recent_visit_rows:
        events = visit["events"]
        if len(events) > 1:
            duration_s = max(0.0, (visit["last_event"] - visit["first_event"]).total_seconds())
            duration_label = f"{int(duration_s)}s" if duration_s < 120 else f"{int(duration_s / 60)}m"
        else:
            duration_label = "—"
        session_key = ""
        if not visit["visitor_id"] and visit["visit_key"].startswith("session:"):
            session_key = visit["visit_key"][len("session:") :]
        recent_sessions.append(
            {
                "visitor_id_short": str(visit["visitor_id"])[:8] if visit["visitor_id"] else "—",
                "visitor_id": str(visit["visitor_id"]) if visit["visitor_id"] else "",
                "session_key": session_key,
                "referrer": referrer_labels.get(visit["referrer_category"], visit["referrer_category"] or "Unknown"),
                "referrer_domain": visit["referrer_domain"] or "",
                "landing_page": visit["landing_page"] or "—",
                "event_count": len(events),
                "first_event": visit["first_event"],
                "last_event": visit["last_event"],
                "duration": duration_label,
                "engaged": _visit_is_engaged(visit),
            }
        )

    # Synthesise referrer insight sentence
    total_visits = sum(r["visits"] for r in referrer_breakdown)
    referrer_insight = ""
    if total_visits:
        parts = []
        for row in referrer_breakdown[:3]:
            pct = round(row["visits"] / total_visits * 100)
            if pct >= 5:
                parts.append(f"{pct}% {row['label']}")
        if parts:
            referrer_insight = "Visit starts: " + ", ".join(parts) + "."

    # rollout_date is computed above for the new/returning split.
    rollout_mature = False
    if rollout_date is not None:
        rollout_mature = (timezone.localdate() - rollout_date).days >= 90

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "referrer_breakdown": referrer_breakdown,
        "referrer_insight": referrer_insight,
        "other_domains": other_domains,
        "new_visitors": new_count,
        "returning_visitors": returning_count,
        "returning_basis": returning_basis,
        "multi_day_visitors": multi_day_count,
        "total_visitors": len(visitor_ids_in_period),
        "returning_rate": _safe_percentage(returning_count, len(visitor_ids_in_period)),
        "site_analytics_rollout_date": rollout_date,
        "site_analytics_rollout_mature": rollout_mature,
        "page_breakdown": page_breakdown,
        "journal_visits": journal_visits,
        "traffic_chart_labels_json": json.dumps(traffic_chart_labels),
        "traffic_chart_series_json": json.dumps(traffic_chart_series),
        "landing_breakdown": landing_breakdown,
        "campaign_breakdown": campaign_breakdown,
        "search_dead_ends": search_dead_ends,
        "top_flows": flow_counts,
        "recent_sessions": recent_sessions,
        "recent_session_total": recent_session_total,
        "recent_session_page": page,
        "recent_session_total_pages": total_pages,
        "recent_session_has_prev": page > 1,
        "recent_session_has_next": page < total_pages,
        "engaged_only": engaged_only,
        "visit_timeout_minutes": int(_VISIT_INACTIVITY_GAP.total_seconds() // 60),
        "active_tab": "traffic",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(
        request, "backend/analytics/traffic.html", context, "backend/analytics/_traffic_panel.html"
    )


def _acquisition_summary(start_ts, end_ts):
    """Counts of newly-created subscribers in window, grouped by source."""
    qs = Subscriber.objects.filter(created__gte=start_ts, created__lte=end_ts)
    totals = dict(qs.values_list("source").annotate(c=Count("id")).values_list("source", "c"))
    by_source = [
        {"source": value, "label": label, "count": totals.get(value, 0)} for value, label in Subscriber.Source.choices
    ]
    total = sum(row["count"] for row in by_source)
    unsubscribed = Subscriber.objects.filter(modified__gte=start_ts, modified__lte=end_ts, subscribed=False).count()
    return {"by_source": by_source, "total": total, "unsubscribed": unsubscribed}


def _recent_subscriber_feed(start_ts, end_ts, limit=20):
    """Feed of recent subscriber activity — CSV imports collapsed to one row per batch."""
    individual = list(
        Subscriber.objects.filter(created__gte=start_ts, created__lte=end_ts)
        .exclude(source=Subscriber.Source.CSV_IMPORT)
        .order_by("-created")
        .values("email", "source", "created")[:limit]
    )
    csvs = list(
        SubscriberCSV.objects.filter(created__gte=start_ts, created__lte=end_ts, processed=True)
        .order_by("-created")
        .values("id", "name", "email_added_count", "created")[:limit]
    )

    source_labels = dict(Subscriber.Source.choices)
    entries = []
    for row in individual:
        entries.append(
            {
                "kind": "individual",
                "email": row["email"],
                "source": row["source"],
                "source_label": source_labels.get(row["source"], row["source"]),
                "when": row["created"],
            }
        )
    for row in csvs:
        entries.append(
            {
                "kind": "csv",
                "csv_id": row["id"],
                "name": row["name"],
                "count": row["email_added_count"] or 0,
                "source": "csv_import",
                "source_label": source_labels.get("csv_import", "CSV import"),
                "when": row["created"],
            }
        )
    entries.sort(key=lambda e: e["when"], reverse=True)
    return entries[:limit]


@login_required
@permission_required(VIEW_NEWSLETTER_STATS, raise_exception=True)
def analytics_email(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request, default_days=180)
    site_analytics_rollout_date = _site_analytics_rollout_date()

    newsletters = list(
        Newsletter.objects.filter(
            is_sent=True,
            send_date__date__gte=start_date,
            send_date__date__lte=end_date,
        ).order_by("-send_date")
    )

    # Batch all open/click stats so we issue 3 queries for all newsletters combined
    # rather than 7+ per newsletter.
    from itertools import groupby as _groupby

    newsletter_ids = [nl.pk for nl in newsletters]

    open_stats = {
        row["newsletter_id"]: row
        for row in NewsletterOpen.objects.filter(newsletter_id__in=newsletter_ids)
        .values("newsletter_id")
        .annotate(
            total=Count("id"),
            human=Count("id", filter=Q(automated=False)),
            human_unique=Count("subscriber", distinct=True, filter=Q(automated=False)),
        )
    }

    click_stats = {
        row["newsletter_id"]: row
        for row in NewsletterClick.objects.filter(newsletter_id__in=newsletter_ids)
        .values("newsletter_id")
        .annotate(
            total=Count("id"),
            human=Count("id", filter=Q(automated=False)),
            human_unique=Count("subscriber", distinct=True, filter=Q(automated=False)),
        )
    }

    # Per-link breakdowns — one query, top 8 per newsletter applied in Python
    link_rows = list(
        NewsletterClick.objects.filter(newsletter_id__in=newsletter_ids, automated=False)
        .exclude(destination_url="")
        .values("newsletter_id", "destination_url")
        .annotate(clicks=Count("id"), unique_subscribers=Count("subscriber", distinct=True))
        .order_by("newsletter_id", "-clicks")
    )
    link_clicks_by_nl = {}
    for nl_id, group in _groupby(link_rows, key=lambda r: r["newsletter_id"]):
        link_clicks_by_nl[nl_id] = list(group)[:8]

    newsletter_rows = []
    for nl in newsletters:
        ostats = open_stats.get(nl.pk, {})
        cstats = click_stats.get(nl.pk, {})
        total_opens_nl = ostats.get("total", 0)
        total_clicks_nl = cstats.get("total", 0)
        total_filtered_opens = ostats.get("human", 0)
        total_filtered_clicks = cstats.get("human", 0)
        human_opens = ostats.get("human_unique", 0)
        human_clicks = cstats.get("human_unique", 0)
        site_analytics_partial = _newsletter_predates_site_analytics(nl)

        post_traffic = None if site_analytics_partial else 0
        if nl.send_date and not site_analytics_partial:
            post_traffic = AnalyticsEvent.objects.filter(
                event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
                automated=False,
                timestamp__gte=nl.send_date,
                timestamp__lte=nl.send_date + datetime.timedelta(days=7),
            ).count()

        newsletter_rows.append(
            {
                "newsletter": nl,
                "emails_sent": nl.emails_sent or 0,
                "human_opens": human_opens,
                "human_open_rate": _safe_percentage(human_opens, nl.emails_sent),
                "human_clicks": human_clicks,
                "human_ctr": _safe_percentage(human_clicks, nl.emails_sent),
                "human_ctor": _safe_percentage(human_clicks, human_opens),
                "automated_open_share": _safe_percentage(
                    max(total_opens_nl - total_filtered_opens, 0), total_opens_nl
                ),
                "automated_click_share": _safe_percentage(
                    max(total_clicks_nl - total_filtered_clicks, 0), total_clicks_nl
                ),
                "post_send_traffic": post_traffic,
                "link_clicks": link_clicks_by_nl.get(nl.pk, []),
                "site_analytics_partial": site_analytics_partial,
            }
        )

    total_subscribers = Subscriber.objects.filter(subscribed=True).count()
    total_sent = Newsletter.objects.filter(is_sent=True).count()

    # Subscriber engagement segmentation (always uses most recent sends, not date-filtered)
    recent_newsletters = list(Newsletter.objects.filter(is_sent=True).order_by("-send_date")[:10])
    recent_nl_ids = [nl.id for nl in recent_newsletters]
    segment_counts = {"highly_engaged": 0, "occasional": 0, "dormant": 0}

    if recent_nl_ids:
        active_subscribers = list(Subscriber.objects.filter(subscribed=True).values_list("id", flat=True))
        nl_count = len(recent_nl_ids)

        seg_opens_qs = NewsletterOpen.objects.filter(newsletter_id__in=recent_nl_ids, automated=False)
        seg_clicks_qs = NewsletterClick.objects.filter(newsletter_id__in=recent_nl_ids, automated=False)
        opens_per_sub = dict(
            seg_opens_qs.values("subscriber_id")
            .annotate(newsletters_opened=Count("newsletter_id", distinct=True))
            .values_list("subscriber_id", "newsletters_opened")
        )
        clicks_per_sub = dict(
            seg_clicks_qs.values("subscriber_id")
            .annotate(newsletters_clicked=Count("newsletter_id", distinct=True))
            .values_list("subscriber_id", "newsletters_clicked")
        )

        for sub_id in active_subscribers:
            opened = opens_per_sub.get(sub_id, 0)
            clicked = clicks_per_sub.get(sub_id, 0)
            open_rate = opened / nl_count if nl_count else 0
            click_rate = clicked / nl_count if nl_count else 0

            if open_rate >= 0.7 and click_rate >= 0.3:
                segment_counts["highly_engaged"] += 1
            elif open_rate >= 0.3 or clicked >= 1:
                segment_counts["occasional"] += 1
            else:
                segment_counts["dormant"] += 1

    # Newsletter lift — engaged views before/after each send
    newsletter_lift = []
    for nl in newsletters:
        if not nl.send_date:
            continue
        if _newsletter_predates_site_analytics(nl):
            newsletter_lift.append(
                {
                    "newsletter": nl,
                    "before": None,
                    "after": None,
                    "lift_pct": None,
                    "site_analytics_partial": True,
                }
            )
            continue
        send_dt = nl.send_date
        before_start = send_dt - datetime.timedelta(days=7)
        after_end = send_dt + datetime.timedelta(days=7)
        lift_qs = AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED, automated=False)
        before_count = lift_qs.filter(timestamp__gte=before_start, timestamp__lt=send_dt).count()
        after_count = lift_qs.filter(timestamp__gte=send_dt, timestamp__lte=after_end).count()
        lift_pct = None
        if before_count:
            lift_pct = round((after_count - before_count) / before_count * 100)
        newsletter_lift.append(
            {
                "newsletter": nl,
                "before": before_count,
                "after": after_count,
                "lift_pct": lift_pct,
                "site_analytics_partial": False,
            }
        )

    # Trend chart data — serialise for Chart.js
    trend_labels = json.dumps([row["newsletter"].send_date.strftime("%-d %b %Y") for row in newsletter_rows if row])
    trend_open_rates = json.dumps(
        [row["human_open_rate"].rstrip("%") if row["human_open_rate"] != "0%" else "0" for row in newsletter_rows]
    )
    trend_ctrs = json.dumps(
        [row["human_ctr"].rstrip("%") if row["human_ctr"] != "0%" else "0" for row in newsletter_rows]
    )

    acquisition = _acquisition_summary(start_ts, end_ts)
    recent_feed = _recent_subscriber_feed(start_ts, end_ts, limit=20)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "newsletter_rows": newsletter_rows,
        "total_subscribers": total_subscribers,
        "total_sent": total_sent,
        "segment_counts": segment_counts,
        "segment_newsletter_count": len(recent_nl_ids),
        "newsletter_lift": newsletter_lift,
        "trend_labels": trend_labels,
        "trend_open_rates": trend_open_rates,
        "trend_ctrs": trend_ctrs,
        "has_partial_site_analytics": any(row["site_analytics_partial"] for row in newsletter_rows),
        "site_analytics_rollout_date": site_analytics_rollout_date,
        "acquisition": acquisition,
        "recent_subscriber_feed": recent_feed,
        "active_tab": "email",
    }
    return _render_analytics(request, "backend/analytics/email.html", context, "backend/analytics/_email_panel.html")


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_search(request):
    """Legacy URL — redirects to editorial."""
    qs = request.GET.urlencode()
    url = reverse("backend:analytics_editorial")
    return redirect(f"{url}?{qs}" if qs else url)


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_journals(request):
    from spanza_journal_watch.backend.models import PubmedArticleUserState, WatchedJournal
    from spanza_journal_watch.cpd.models import CPDReport

    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = _base_event_qs(request, start_ts, end_ts)
    journal_events = human_events.filter(event_type__in=_JOURNAL_EVENT_TYPES)
    journal_visits = _build_derived_visits_cached(journal_events)

    # ── Headline metrics ────────────────────────────────────────────
    total_visits = len(journal_visits)
    visitor_ids_in_period = {visit["visitor_id"] for visit in journal_visits if visit["visitor_id"]}
    unique_visitors = len(visitor_ids_in_period)
    returning_visitors = (
        AnalyticsEvent.objects.filter(
            event_type__in=_JOURNAL_EVENT_TYPES,
            visitor_id__in=visitor_ids_in_period,
            timestamp__lt=start_ts,
        )
        .values("visitor_id")
        .distinct()
        .count()
    )
    returning_rate = _safe_percentage(returning_visitors, unique_visitors) if unique_visitors else "–"

    # ── User actions (from PubmedArticleUserState, within date range) ─
    states_in_range = PubmedArticleUserState.objects.all()
    total_stars = states_in_range.filter(starred_at__gte=start_ts, starred_at__lte=end_ts).count()
    total_archives = states_in_range.filter(read_at__gte=start_ts, read_at__lte=end_ts).count()
    total_recommends = states_in_range.filter(recommended_at__gte=start_ts, recommended_at__lte=end_ts).count()
    total_full_text = states_in_range.filter(
        full_text_clicked_at__gte=start_ts, full_text_clicked_at__lte=end_ts
    ).count()

    # ── CPD reports ─────────────────────────────────────────────────
    cpd_reports = CPDReport.objects.filter(created__gte=start_ts, created__lte=end_ts)
    cpd_generated = cpd_reports.count()
    cpd_users = cpd_reports.values("user").distinct().count()

    # ── Engagement funnel ───────────────────────────────────────────
    # visits → stars → archives → full text → recommends
    funnel = [
        {"label": "Journal visits", "count": total_visits, "color": "secondary"},
        {"label": "Articles starred", "count": total_stars, "color": "warning"},
        {"label": "Articles archived", "count": total_archives, "color": "secondary"},
        {"label": "Full text clicked", "count": total_full_text, "color": "success"},
        {"label": "Recommended for review", "count": total_recommends, "color": "primary"},
    ]
    funnel_max = max((f["count"] for f in funnel), default=1) or 1

    # ── Top journals by engagement ──────────────────────────────────
    journal_select_events = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_SELECT)
    journal_counter = Counter()
    journal_id_map = {}
    for meta in journal_select_events.values_list("metadata", flat=True):
        jid = (meta or {}).get("journal_id")
        jname = (meta or {}).get("journal_name", "")
        if jid:
            journal_counter[jid] += 1
            if jname:
                journal_id_map[jid] = jname

    # Enrich with WatchedJournal names for IDs we haven't seen names for
    missing_ids = [jid for jid in journal_counter if jid not in journal_id_map]
    if missing_ids:
        for wj in WatchedJournal.objects.filter(pk__in=missing_ids).values("pk", "name"):
            journal_id_map[wj["pk"]] = wj["name"]

    top_journals = [
        {"name": journal_id_map.get(jid, f"Journal {jid}"), "views": count}
        for jid, count in journal_counter.most_common(10)
    ]

    # ── Active reading list users ───────────────────────────────────
    active_users_qs = (
        states_in_range.filter(starred_at__gte=start_ts, starred_at__lte=end_ts)
        .values("user__email", "user__name")
        .annotate(
            star_count=Count("id", filter=Q(starred_at__gte=start_ts, starred_at__lte=end_ts)),
            archive_count=Count("id", filter=Q(read_at__gte=start_ts, read_at__lte=end_ts)),
            recommend_count=Count("id", filter=Q(recommended_at__gte=start_ts, recommended_at__lte=end_ts)),
        )
        .order_by("-star_count")[:10]
    )
    active_users = list(active_users_qs)

    # ── Weekly trend ────────────────────────────────────────────────
    star_events = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_STAR)
    visit_buckets = _weekly_visit_buckets(journal_visits)
    star_buckets = _weekly_buckets(star_events)
    trend_labels = json.dumps([b["label"] for b in visit_buckets])
    trend_visits = json.dumps([b["count"] for b in visit_buckets])
    trend_stars = json.dumps([b["count"] for b in star_buckets])

    # ── Cumulative reading list stats (all-time) ────────────────────
    total_reading_list_users = states_in_range.filter(starred_at__isnull=False).values("user").distinct().count()
    total_items_starred_alltime = states_in_range.filter(starred_at__isnull=False).count()
    avg_items_per_user = (
        round(total_items_starred_alltime / total_reading_list_users, 1) if total_reading_list_users else 0
    )

    # ── Feature scorecard with period comparisons ──────────────────
    period_days = (end_date - start_date).days
    prev_end = start_date - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=period_days)
    prev_start_ts = timezone.make_aware(datetime.datetime.combine(prev_start, datetime.time.min))
    prev_end_ts = timezone.make_aware(datetime.datetime.combine(prev_end, datetime.time.max))

    prev_events = AnalyticsEvent.objects.filter(
        timestamp__gte=prev_start_ts, timestamp__lte=prev_end_ts, automated=False
    )

    # Suppress deltas when the comparison period predates the analytics era.
    _rollout_date = _site_analytics_rollout_date()
    comparison_reliable = _rollout_date is not None and prev_start >= _rollout_date

    def _delta(current, previous):
        return _pct_change(current, previous) if comparison_reliable else None

    prev_visits = len(_build_derived_visits(prev_events.filter(event_type__in=_JOURNAL_EVENT_TYPES)))
    prev_stars = states_in_range.filter(starred_at__gte=prev_start_ts, starred_at__lte=prev_end_ts).count()
    prev_searches = (
        prev_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
        .exclude(metadata__query="")
        .exclude(metadata__query__isnull=True)
        .count()
    )

    share_event_types = [
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    ]
    total_shares = human_events.filter(event_type__in=share_event_types).count()
    prev_shares = prev_events.filter(event_type__in=share_event_types).count()

    total_searches = (
        human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
        .exclude(metadata__query="")
        .exclude(metadata__query__isnull=True)
        .count()
    )
    search_clicks = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK).count()
    search_ctr = _safe_percentage(search_clicks, total_searches) if total_searches else "–"

    prev_cpd = CPDReport.objects.filter(created__gte=prev_start_ts, created__lte=prev_end_ts).count()

    feature_scorecard = [
        {
            "name": "Journal visits",
            "metric": total_visits,
            "delta": _delta(total_visits, prev_visits),
            "secondary": f"{returning_rate} returning",
        },
        {
            "name": "Reading Lists",
            "metric": total_stars,
            "delta": _delta(total_stars, prev_stars),
            "secondary": f"{total_reading_list_users} users",
        },
        {
            "name": "Search",
            "metric": total_searches,
            "delta": _delta(total_searches, prev_searches),
            "secondary": f"{search_ctr} CTR",
        },
        {
            "name": "Sharing",
            "metric": total_shares,
            "delta": _delta(total_shares, prev_shares),
            "secondary": "",
        },
        {
            "name": "CPD Reports",
            "metric": cpd_generated,
            "delta": _delta(cpd_generated, prev_cpd),
            "secondary": f"{cpd_users} user{'s' if cpd_users != 1 else ''}",
        },
    ]

    comparison_label = f"vs {prev_start.strftime('%-d %b')} – {prev_end.strftime('%-d %b')}"

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_visits": total_visits,
        "unique_visitors": unique_visitors,
        "returning_visitors": returning_visitors,
        "returning_rate": returning_rate,
        "total_stars": total_stars,
        "total_archives": total_archives,
        "total_recommends": total_recommends,
        "total_full_text": total_full_text,
        "cpd_generated": cpd_generated,
        "cpd_users": cpd_users,
        "funnel": funnel,
        "funnel_max": funnel_max,
        "top_journals": top_journals,
        "active_users": active_users,
        "trend_labels": trend_labels,
        "trend_visits": trend_visits,
        "trend_stars": trend_stars,
        "total_reading_list_users": total_reading_list_users,
        "avg_items_per_user": avg_items_per_user,
        "feature_scorecard": feature_scorecard,
        "visit_timeout_minutes": int(_VISIT_INACTIVITY_GAP.total_seconds() // 60),
        "comparison_label": comparison_label,
        "active_tab": "journals",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(
        request, "backend/analytics/journals.html", context, "backend/analytics/_journals_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_visitor(request, visitor_id):
    """Per-visitor journey: all visits from a given visitor_id, reverse-chrono."""
    today = timezone.localdate()
    lookback_days = 365
    start_ts = timezone.make_aware(
        datetime.datetime.combine(today - datetime.timedelta(days=lookback_days), datetime.time.min)
    )
    end_ts = timezone.make_aware(datetime.datetime.combine(today, datetime.time.max))

    events_qs = AnalyticsEvent.objects.filter(
        visitor_id=visitor_id, timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False
    )
    visits = sorted(_build_derived_visits_cached(events_qs), key=lambda v: v["last_event"], reverse=True)

    referrer_labels = {
        "newsletter": "Newsletter",
        "search": "Search engine",
        "social": "Social media",
        "direct": "Direct",
        "internal": "Internal",
        "other": "Other",
        "": "Unknown",
    }

    visit_rows = []
    for visit in visits:
        if len(visit["events"]) > 1:
            duration_s = max(0.0, (visit["last_event"] - visit["first_event"]).total_seconds())
            duration_label = f"{int(duration_s)}s" if duration_s < 120 else f"{int(duration_s / 60)}m"
        else:
            duration_label = "—"
        visit_rows.append(
            {
                "first_event": visit["first_event"],
                "last_event": visit["last_event"],
                "duration": duration_label,
                "event_count": len(visit["events"]),
                "referrer": referrer_labels.get(visit["referrer_category"], visit["referrer_category"] or "Unknown"),
                "referrer_domain": visit["referrer_domain"] or "",
                "landing_page": visit["landing_page"] or "—",
                "engaged": _visit_is_engaged(visit),
            }
        )

    first_seen = events_qs.order_by("timestamp").values_list("timestamp", flat=True).first()
    last_seen = events_qs.order_by("-timestamp").values_list("timestamp", flat=True).first()

    context = {
        "visitor_id": str(visitor_id),
        "visit_count": len(visit_rows),
        "visits": visit_rows,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "lookback_days": lookback_days,
        "active_tab": "traffic",
    }
    return render(request, "backend/analytics/visitor.html", context)


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_review_timeline(request, review_pk):
    """Per-review event timeline: recent events that hit this review."""
    review = Review.objects.filter(pk=review_pk).first()
    if review is None:
        return redirect("backend:analytics_editorial")

    today = timezone.localdate()
    lookback_days = 180
    start_ts = timezone.make_aware(
        datetime.datetime.combine(today - datetime.timedelta(days=lookback_days), datetime.time.min)
    )
    end_ts = timezone.make_aware(datetime.datetime.combine(today, datetime.time.max))

    review_ct = ContentType.objects.get_for_model(Review)
    events_qs = AnalyticsEvent.objects.filter(
        content_type=review_ct,
        object_id=review.pk,
        timestamp__gte=start_ts,
        timestamp__lte=end_ts,
        automated=False,
    ).order_by("-timestamp")

    try:
        page = max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    page_size = 100
    total_events = events_qs.count()
    total_pages = max(1, (total_events + page_size - 1) // page_size)
    page = min(page, total_pages)
    start_idx = (page - 1) * page_size

    page_events = list(
        events_qs.values(
            "id", "event_type", "timestamp", "visitor_id", "referrer_category", "referrer_domain", "metadata"
        )[start_idx : start_idx + page_size]
    )
    event_extras, _ = _enrich_event_details(page_events)

    totals = events_qs.aggregate(
        impressions=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
        sustained=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
        full_text=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
        avg_duration=Avg("duration_ms", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
    )

    timeline = []
    for row in page_events:
        extra = event_extras.get(row["id"])
        timeline.append(
            {
                "timestamp": row["timestamp"],
                "type": _FLOW_LABELS.get(row["event_type"], row["event_type"]),
                "visitor_id": str(row["visitor_id"]) if row["visitor_id"] else "",
                "visitor_id_short": str(row["visitor_id"])[:8] if row["visitor_id"] else "—",
                "referrer": row["referrer_category"] or "",
                "referrer_domain": row["referrer_domain"] or "",
                "detail": _format_event_detail(row, extra, None),
            }
        )

    context = {
        "review": review,
        "lookback_days": lookback_days,
        "total_events": total_events,
        "impressions": totals["impressions"] or 0,
        "sustained_views": totals["sustained"] or 0,
        "full_text_clicks": totals["full_text"] or 0,
        "avg_duration_s": round((totals["avg_duration"] or 0) / 1000, 1),
        "timeline": timeline,
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "active_tab": "content",
    }
    return render(request, "backend/analytics/review_timeline.html", context)


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_visit_events(request):
    """HTMX fragment: events for a single derived visit, bounded by timestamps."""
    start_iso = (request.GET.get("start") or "").strip()
    end_iso = (request.GET.get("end") or "").strip()
    visitor_id = (request.GET.get("visitor_id") or "").strip()
    session_key = (request.GET.get("session_key") or "").strip()
    if not start_iso or not end_iso:
        return HttpResponseBadRequest("start and end required")
    if not visitor_id and not session_key:
        return HttpResponseBadRequest("visitor_id or session_key required")
    try:
        start_ts = datetime.datetime.fromisoformat(start_iso)
        end_ts = datetime.datetime.fromisoformat(end_iso)
    except ValueError:
        return HttpResponseBadRequest("Invalid timestamps")

    qs = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False).order_by(
        "timestamp", "id"
    )
    if visitor_id:
        qs = qs.filter(visitor_id=visitor_id)
    else:
        qs = qs.filter(session_key=session_key)

    event_rows = list(qs.values("id", "event_type", "timestamp", "metadata")[:50])
    extras, titles = _enrich_event_details(event_rows)
    events = [
        {
            "type": _FLOW_LABELS.get(e["event_type"], e["event_type"]),
            "timestamp": e["timestamp"],
            "detail": _format_event_detail(e, extras.get(e["id"]), titles.get(e["id"])),
        }
        for e in event_rows
    ]
    return render(request, "backend/analytics/_visit_events.html", {"events": events})
