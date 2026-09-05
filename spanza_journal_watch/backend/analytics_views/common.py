"""Shared plumbing for the analytics panels.

Date ranges, the human-filtered event queryset, panel rendering and weekly buckets.
"""

import datetime

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _parse_iso_date,
)
from spanza_journal_watch.newsletter.models import Newsletter


def _pct_change(current, previous):
    """Return percentage change as an integer, or None if no previous data."""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


VIEW_SITE_ANALYTICS = "backend.view_site_analytics"
VIEW_NEWSLETTER_STATS = "backend.view_newsletter_stats"


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
