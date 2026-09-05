"""The Journals panel: journal browser usage."""

import datetime
import json
from collections import Counter

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
    _site_analytics_rollout_date,
)

from .benchmarks import _confidence_summary
from .common import (
    VIEW_SITE_ANALYTICS,
    _base_event_qs,
    _date_range_from_request,
    _pct_change,
    _render_analytics,
    _weekly_buckets,
)
from .visits import (
    _JOURNAL_EVENT_TYPES,
    _VISIT_INACTIVITY_GAP,
    _build_derived_visits,
    _build_derived_visits_cached,
    _weekly_visit_buckets,
)


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
