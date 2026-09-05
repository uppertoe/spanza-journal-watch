"""The Overview panel."""

import datetime
from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
    AutomatedRequestCount,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _newsletter_predates_site_analytics,
    _safe_percentage,
    _site_analytics_rollout_date,
)
from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.submissions.models import Review

from .benchmarks import _confidence_summary, _is_one_step_visit
from .common import (
    VIEW_SITE_ANALYTICS,
    _base_event_qs,
    _date_range_from_request,
    _engaged_human_count,
    _newsletter_send_weeks,
    _pct_change,
    _render_analytics,
    _weekly_buckets,
)
from .visits import (
    _LOW_SAMPLE_THRESHOLD,
    _PAGE_SECTION_LABELS,
    _build_derived_visits_cached,
    _derive_page_section,
    _normalise_search_query,
    _rank_rows,
)


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
