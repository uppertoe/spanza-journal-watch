"""The Traffic panel: acquisition and referrers."""

import json
from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
    _site_analytics_rollout_date,
)

from .benchmarks import _confidence_summary, _is_one_step_visit
from .common import VIEW_SITE_ANALYTICS, _base_event_qs, _date_range_from_request, _render_analytics
from .flows import _compute_top_flows, _visit_is_engaged
from .visits import (
    _JOURNAL_EVENT_TYPES,
    _LOW_SAMPLE_THRESHOLD,
    _PAGE_SECTION_LABELS,
    _VISIT_INACTIVITY_GAP,
    _build_derived_visits_cached,
    _derive_page_section,
    _normalise_search_query,
    _split_new_returning,
    _weekly_visits_by_referrer,
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

    # Recent visit explorer — defaults to engaged-only (the high-signal view);
    # an explicit engaged_only=0 opts back into the full raw-traffic list.
    engaged_only = request.GET.get("engaged_only") not in ("0", "false", "no")
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
