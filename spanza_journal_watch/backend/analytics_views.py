"""
Analytics views for the editorial backend.

Six focused pages, each answering a specific question about how
readers interact with the site.
"""

import datetime
import json
from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent, NewsletterClick, NewsletterOpen
from spanza_journal_watch.backend.views import (
    _build_rate_row,
    _parse_iso_date,
    _safe_percentage,
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


def _render_analytics(request, template, context, panel_template=None):
    if request.headers.get("HX-Request") and panel_template:
        return render(request, panel_template, context)
    return render(request, template, context)


def _weekly_buckets(qs, timestamp_field="timestamp", weeks=26):
    today = timezone.localdate()
    buckets = []
    for i in range(weeks - 1, -1, -1):
        week_start = today - datetime.timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + datetime.timedelta(days=6)
        start_ts = timezone.make_aware(datetime.datetime.combine(week_start, datetime.time.min))
        end_ts = timezone.make_aware(datetime.datetime.combine(week_end, datetime.time.max))
        count = qs.filter(
            **{
                f"{timestamp_field}__gte": start_ts,
                f"{timestamp_field}__lte": end_ts,
            }
        ).count()
        buckets.append(
            {
                "label": week_start.strftime("%-d %b"),
                "count": count,
                "week_start": week_start.isoformat(),
            }
        )
    return buckets


def _weekly_visitors_by_referrer(qs, categories, weeks=26):
    """Return weekly labels and {category: [count, ...]} of unique visitors."""
    today = timezone.localdate()
    labels = []
    series = {cat: [] for cat in categories}
    for i in range(weeks - 1, -1, -1):
        week_start = today - datetime.timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + datetime.timedelta(days=6)
        start_ts = timezone.make_aware(datetime.datetime.combine(week_start, datetime.time.min))
        end_ts = timezone.make_aware(datetime.datetime.combine(week_end, datetime.time.max))
        week_qs = qs.filter(timestamp__gte=start_ts, timestamp__lte=end_ts).exclude(visitor_id=None)
        labels.append(week_start.strftime("%-d %b"))
        for cat in categories:
            if cat == "other":
                count = (
                    week_qs.exclude(referrer_category__in=[c for c in categories if c != "other"])
                    .values("visitor_id")
                    .distinct()
                    .count()
                )
            else:
                count = week_qs.filter(referrer_category=cat).values("visitor_id").distinct().count()
            series[cat].append(count)
    return labels, series


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
    "review_open": "View review",
    "review_engaged": "Read review",
    "review_full_text_click": "Full text",
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
}


def _compute_top_flows(human_events, start_ts, end_ts):
    """Return the top 8 two-step transitions across sessions."""
    pairs = (
        human_events.filter(session_sequence__gte=1, session_sequence__lte=10)
        .values("session_key", "event_type", "session_sequence")
        .order_by("session_key", "session_sequence")
    )
    events_by_session = defaultdict(list)
    for row in pairs:
        events_by_session[row["session_key"]].append(row["event_type"])

    transition_counter = Counter()
    for _session_key, event_types in events_by_session.items():
        seen = set()
        for i in range(len(event_types) - 1):
            pair = (event_types[i], event_types[i + 1])
            if pair not in seen:
                transition_counter[pair] += 1
                seen.add(pair)

    return [
        {
            "from": _FLOW_LABELS.get(a, a),
            "to": _FLOW_LABELS.get(b, b),
            "count": count,
        }
        for (a, b), count in transition_counter.most_common(8)
    ]


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_overview(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)
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

    total_opens = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN).count()
    total_engaged = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).count()
    total_full_text = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK).count()
    total_shares = review_events.filter(event_type__in=share_event_types).count()
    avg_dwell_ms = (
        review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).aggregate(avg=Avg("duration_ms"))[
            "avg"
        ]
        or 0
    )
    search_count = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH).count()
    avg_scroll = (
        review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)
        .exclude(scroll_depth=None)
        .aggregate(avg=Avg("scroll_depth"))["avg"]
    )
    avg_scroll_depth = round(avg_scroll) if avg_scroll is not None else None

    # Previous period for comparison
    period_days = (end_date - start_date).days
    prev_end = start_date - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=period_days)
    prev_start_ts = timezone.make_aware(datetime.datetime.combine(prev_start, datetime.time.min))
    prev_end_ts = timezone.make_aware(datetime.datetime.combine(prev_end, datetime.time.max))

    prev_human = AnalyticsEvent.objects.filter(
        timestamp__gte=prev_start_ts, timestamp__lte=prev_end_ts, automated=False
    )
    prev_review = prev_human.filter(content_type=review_ct)
    prev_opens = prev_review.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN).count()
    prev_engaged = prev_review.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).count()
    prev_dwell_ms = (
        prev_review.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).aggregate(avg=Avg("duration_ms"))["avg"]
        or 0
    )
    prev_full_text = prev_review.filter(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK).count()
    prev_shares = prev_review.filter(event_type__in=share_event_types).count()
    prev_searches = prev_human.filter(event_type=AnalyticsEvent.EventType.SEARCH).count()
    prev_scroll = (
        prev_review.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)
        .exclude(scroll_depth=None)
        .aggregate(avg=Avg("scroll_depth"))["avg"]
    )
    prev_scroll_depth = round(prev_scroll) if prev_scroll is not None else None

    engaged_qs = AnalyticsEvent.objects.filter(
        event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
        automated=False,
    )
    weekly_trend = _weekly_buckets(engaged_qs)
    newsletter_sends = _newsletter_send_weeks(weeks=26)

    recent_newsletters = list(Newsletter.objects.filter(is_sent=True).order_by("-send_date")[:4])
    newsletter_lift = []
    for nl in recent_newsletters:
        if not nl.send_date:
            continue
        send_dt = nl.send_date
        before_start = send_dt - datetime.timedelta(days=7)
        after_end = send_dt + datetime.timedelta(days=7)
        before_count = AnalyticsEvent.objects.filter(
            event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
            automated=False,
            timestamp__gte=before_start,
            timestamp__lt=send_dt,
        ).count()
        after_count = AnalyticsEvent.objects.filter(
            event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
            automated=False,
            timestamp__gte=send_dt,
            timestamp__lte=after_end,
        ).count()
        lift_pct = None
        if before_count:
            lift_pct = round((after_count - before_count) / before_count * 100)
        newsletter_lift.append(
            {
                "newsletter": nl,
                "before": before_count,
                "after": after_count,
                "lift_pct": lift_pct,
            }
        )

    human_event_count = human_events.count()
    subscriber_events = human_events.filter(
        human_confidence=AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN
    ).count()

    context = {
        "start_date": start_date,
        "end_date": end_date,
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
        "delta_opens": _pct_change(total_opens, prev_opens),
        "delta_engaged": _pct_change(total_engaged, prev_engaged),
        "delta_dwell": _pct_change(avg_dwell_ms, prev_dwell_ms),
        "delta_full_text": _pct_change(total_full_text, prev_full_text),
        "delta_shares": _pct_change(total_shares, prev_shares),
        "delta_searches": _pct_change(search_count, prev_searches),
        "delta_scroll": _pct_change(avg_scroll_depth or 0, prev_scroll_depth or 0) if avg_scroll_depth else None,
        "comparison_label": f"vs {prev_start.strftime('%-d %b')} – {prev_end.strftime('%-d %b')}",
        "active_tab": "overview",
    }
    return _render_analytics(
        request, "backend/analytics/overview.html", context, "backend/analytics/_overview_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_content(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)
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
    total_opens = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN).count()
    total_engaged = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).count()
    total_full_text = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK).count()
    total_shares = review_events.filter(event_type__in=share_event_types).count()

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            avg_dwell_ms=Avg("duration_ms", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            avg_scroll=Avg("scroll_depth", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
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
    }

    top_reviews = []
    journal_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})
    tag_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})

    for row in review_summary_rows:
        review = reviews_by_id.get(row["object_id"])
        if not review:
            continue
        score = row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
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
                "avg_scroll_depth": round(row["avg_scroll"]) if row["avg_scroll"] is not None else None,
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

    top_reviews = sorted(top_reviews, key=lambda x: x["engagement_score"], reverse=True)[:15]
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

    share_counts = {
        "Copy link": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK).count(),
        "Email": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL).count(),
        "Native share": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE).count(),
        "Bluesky": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY).count(),
        "X": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_X).count(),
        "Facebook": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK).count(),
    }
    share_breakdown = [{"label": k, "count": v} for k, v in share_counts.items() if v]

    # Share-to-visit attribution — count downstream visits from share tokens
    share_token_events = human_events.exclude(share_token="").values("share_token").distinct()
    share_attributed_visits = share_token_events.count()

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

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_opens": total_opens,
        "total_engaged": total_engaged,
        "total_full_text": total_full_text,
        "total_shares": total_shares,
        "engaged_rate": _safe_percentage(total_engaged, total_opens),
        "full_text_ctr": _safe_percentage(total_full_text, total_opens),
        "share_rate": _safe_percentage(total_shares, total_opens),
        "top_reviews": top_reviews,
        "top_journals": top_journals,
        "top_tags": top_tags,
        "tag_type_breakdown": tag_type_breakdown,
        "review_type_breakdown": [
            _group_summary("Featured reviews", featured_rows),
            _group_summary("Standard reviews", standard_rows),
        ],
        "share_breakdown": share_breakdown,
        "share_attributed_visits": share_attributed_visits,
        "active_tab": "content",
    }
    return _render_analytics(
        request, "backend/analytics/content.html", context, "backend/analytics/_content_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_traffic(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)

    referrer_rows = list(
        human_events.values("referrer_category")
        .annotate(
            events=Count("id"),
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
        )
        .order_by("-events")
    )
    referrer_labels = {
        "newsletter": "Newsletter",
        "search": "Search engine",
        "social": "Social media",
        "direct": "Direct",
        "internal": "Internal",
        "other": "Other",
        "": "Unknown",
    }
    referrer_breakdown = [
        {
            "label": referrer_labels.get(row["referrer_category"], row["referrer_category"] or "Unknown"),
            "events": row["events"],
            "opens": row["opens"],
            "engaged": row["engaged"],
            "engaged_rate": _safe_percentage(row["engaged"], row["opens"]),
        }
        for row in referrer_rows
    ]

    # Top referrer domains for "Other" traffic
    other_domains = list(
        human_events.filter(referrer_category="other")
        .exclude(referrer_domain="")
        .values("referrer_domain")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )

    visitor_ids_in_period = set(human_events.exclude(visitor_id=None).values_list("visitor_id", flat=True).distinct())
    returning_count = 0
    new_count = 0
    if visitor_ids_in_period:
        returning_ids = set(
            AnalyticsEvent.objects.filter(
                visitor_id__in=visitor_ids_in_period,
                timestamp__lt=start_ts,
            )
            .values_list("visitor_id", flat=True)
            .distinct()
        )
        returning_count = len(returning_ids)
        new_count = len(visitor_ids_in_period) - returning_count

    page_visit_rows = list(human_events.filter(event_type=AnalyticsEvent.EventType.PAGE_VISIT).values("metadata"))
    page_counts = Counter()
    for row in page_visit_rows:
        page = (row["metadata"] or {}).get("page", "unknown")
        page_counts[page] += 1

    page_labels = {
        "home": "Homepage",
        "issue": "Issue pages",
        "tag": "Tag pages",
        "journals": "Journals browser",
        "search": "Search",
    }
    page_breakdown = [
        {"label": page_labels.get(page, page), "visits": count} for page, count in page_counts.most_common()
    ]

    journal_visits = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT).count()

    traffic_categories = ["newsletter", "search", "social", "direct", "other"]
    traffic_chart_labels, traffic_chart_series = _weekly_visitors_by_referrer(
        AnalyticsEvent.objects.filter(automated=False),
        categories=traffic_categories,
    )

    # Landing page distribution — first event per session
    landing_counts = Counter()
    landing_rows = list(
        human_events.filter(session_sequence=1).exclude(landing_page="").values_list("landing_page", flat=True)
    )
    for path in landing_rows:
        landing_counts[path] += 1
    landing_breakdown = [{"path": path, "visits": count} for path, count in landing_counts.most_common(10)]

    # UTM campaign breakdown
    utm_rows = list(
        human_events.filter(metadata__utm_source__isnull=False)
        .exclude(metadata__utm_source="")
        .values_list("metadata", flat=True)
    )
    campaign_counts = Counter()
    for meta in utm_rows:
        source = (meta or {}).get("utm_source", "")
        medium = (meta or {}).get("utm_medium", "")
        campaign = (meta or {}).get("utm_campaign", "")
        if source:
            label = source
            if medium:
                label += f" / {medium}"
            if campaign:
                label += f" / {campaign}"
            campaign_counts[label] += 1
    campaign_breakdown = [{"label": label, "events": count} for label, count in campaign_counts.most_common(10)]

    # Top session flows — most common 2-step transitions
    flow_counts = _compute_top_flows(human_events, start_ts, end_ts)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "referrer_breakdown": referrer_breakdown,
        "other_domains": other_domains,
        "new_visitors": new_count,
        "returning_visitors": returning_count,
        "total_visitors": len(visitor_ids_in_period),
        "returning_rate": _safe_percentage(returning_count, len(visitor_ids_in_period)),
        "page_breakdown": page_breakdown,
        "journal_visits": journal_visits,
        "traffic_chart_labels_json": json.dumps(traffic_chart_labels),
        "traffic_chart_series_json": json.dumps(traffic_chart_series),
        "landing_breakdown": landing_breakdown,
        "campaign_breakdown": campaign_breakdown,
        "top_flows": flow_counts,
        "active_tab": "traffic",
    }
    return _render_analytics(
        request, "backend/analytics/traffic.html", context, "backend/analytics/_traffic_panel.html"
    )


@login_required
@permission_required(VIEW_NEWSLETTER_STATS, raise_exception=True)
def analytics_email(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request, default_days=180)

    newsletters = list(
        Newsletter.objects.filter(
            is_sent=True,
            send_date__date__gte=start_date,
            send_date__date__lte=end_date,
        ).order_by("-send_date")
    )

    newsletter_rows = []
    for nl in newsletters:
        opens_qs = NewsletterOpen.objects.filter(newsletter=nl)
        clicks_qs = NewsletterClick.objects.filter(newsletter=nl)
        total_opens = opens_qs.count()
        human_opens = opens_qs.filter(automated=False).values("subscriber").distinct().count()
        human_clicks = clicks_qs.filter(automated=False).values("subscriber").distinct().count()

        post_traffic = 0
        if nl.send_date:
            post_traffic = AnalyticsEvent.objects.filter(
                event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
                automated=False,
                timestamp__gte=nl.send_date,
                timestamp__lte=nl.send_date + datetime.timedelta(days=7),
            ).count()

        # Per-link click breakdown for this newsletter
        link_clicks = list(
            clicks_qs.filter(automated=False)
            .exclude(destination_url="")
            .values("destination_url")
            .annotate(
                clicks=Count("id"),
                unique_subscribers=Count("subscriber", distinct=True),
            )
            .order_by("-clicks")[:8]
        )

        newsletter_rows.append(
            {
                "newsletter": nl,
                "emails_sent": nl.emails_sent or 0,
                "human_opens": human_opens,
                "human_open_rate": _safe_percentage(human_opens, nl.emails_sent),
                "human_clicks": human_clicks,
                "human_ctr": _safe_percentage(human_clicks, human_opens),
                "automated_share": _safe_percentage(max(total_opens - human_opens, 0), total_opens)
                if total_opens
                else "0%",
                "post_send_traffic": post_traffic,
                "link_clicks": link_clicks,
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

        opens_per_sub = dict(
            NewsletterOpen.objects.filter(newsletter_id__in=recent_nl_ids, automated=False)
            .values("subscriber_id")
            .annotate(newsletters_opened=Count("newsletter_id", distinct=True))
            .values_list("subscriber_id", "newsletters_opened")
        )
        clicks_per_sub = dict(
            NewsletterClick.objects.filter(newsletter_id__in=recent_nl_ids, automated=False)
            .values("subscriber_id")
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

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "newsletter_rows": newsletter_rows,
        "total_subscribers": total_subscribers,
        "total_sent": total_sent,
        "segment_counts": segment_counts,
        "segment_newsletter_count": len(recent_nl_ids),
        "active_tab": "email",
    }
    return _render_analytics(request, "backend/analytics/email.html", context, "backend/analytics/_email_panel.html")


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_search(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)

    search_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
    search_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK)

    search_query_counter = Counter()
    zero_result_counter = Counter()
    for metadata in search_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1

    click_counter = Counter()
    for metadata in search_click_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        click_counter[label] += 1

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

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_searches": search_events.count(),
        "search_click_count": search_click_events.count(),
        "search_ctr": _safe_percentage(search_click_events.count(), search_events.count()),
        "search_insights": search_insights,
        "zero_result_queries": zero_result_queries[:10],
        "weekly_searches": weekly_searches,
        "active_tab": "search",
    }
    return _render_analytics(request, "backend/analytics/search.html", context, "backend/analytics/_search_panel.html")


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_journals(request):
    from spanza_journal_watch.backend.models import PubmedArticleUserState, WatchedJournal
    from spanza_journal_watch.cpd.models import CPDReport

    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)

    # ── Headline metrics ────────────────────────────────────────────
    journal_events = human_events.filter(
        event_type__in=[
            AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
            AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT,
            AnalyticsEvent.EventType.JOURNAL_FULL_TEXT_CLICK,
            AnalyticsEvent.EventType.JOURNAL_STAR,
            AnalyticsEvent.EventType.JOURNAL_SELECT,
        ]
    )
    total_visits = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT).count()
    unique_visitors = journal_events.exclude(visitor_id=None).values("visitor_id").distinct().count()
    returning_visitors = (
        journal_events.exclude(visitor_id=None)
        .values("visitor_id")
        .annotate(visit_count=Count("id"))
        .filter(visit_count__gte=2)
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
        {"label": "Browser visits", "count": total_visits, "color": "secondary"},
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
    visit_events = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT)
    star_events = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_STAR)
    visit_buckets = _weekly_buckets(visit_events)
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
        "active_tab": "journals",
    }
    return _render_analytics(
        request, "backend/analytics/journals.html", context, "backend/analytics/_journals_panel.html"
    )
