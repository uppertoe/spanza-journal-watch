"""Per-review timeline."""

import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.submissions.models import Review

from .benchmarks import _referrer_source_breakdown
from .common import VIEW_SITE_ANALYTICS, _weekly_buckets
from .flows import _FLOW_LABELS, _enrich_event_details, _format_event_detail


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

    # Views over time — weekly REVIEW_OPEN counts for this review.
    open_events = events_qs.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)
    weekly_opens = _weekly_buckets(open_events, weeks=26)
    # How readers reached this review (referrer mix on its opens).
    referrer_breakdown = _referrer_source_breakdown(open_events)

    context = {
        "review": review,
        "lookback_days": lookback_days,
        "total_events": total_events,
        "impressions": totals["impressions"] or 0,
        "sustained_views": totals["sustained"] or 0,
        "full_text_clicks": totals["full_text"] or 0,
        "avg_duration_s": round((totals["avg_duration"] or 0) / 1000, 1),
        "weekly_opens": weekly_opens,
        "referrer_breakdown": referrer_breakdown,
        "timeline": timeline,
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "active_tab": "content",
    }
    return render(request, "backend/analytics/review_timeline.html", context)
