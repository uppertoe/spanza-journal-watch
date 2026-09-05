"""Per-visitor detail and the visit event list."""

import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)

from .common import VIEW_SITE_ANALYTICS
from .flows import _FLOW_LABELS, _enrich_event_details, _format_event_detail, _visit_is_engaged
from .visits import _build_derived_visits_cached


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
