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


# "Engaged human" KPI: a distinct visitor counts once they take a *deliberate*
# action (DELIBERATE_INTERACTION_EVENT_TYPES), or once a matched subscriber also
# runs JavaScript. Passive scroll and bare `search` submissions are excluded — a
# 2026-06 prod audit showed JS-executing bots game both. A subscriber match alone
# is not enough: hospital link scanners follow newsletter links carrying the
# subscriber's token, so a 2026-10 audit found most "subscriber" visitors were a
# single scanner hit with no JS. The same event-type constant gates the UA-cohort
# bot sweeper, so "engaged" and "not swept as a bot" agree.
def _engaged_visitor_qs(events_qs):
    """``visitor_id`` values (one per visitor) of the engaged humans in ``events_qs``."""
    return (
        events_qs.filter(visitor_id__isnull=False)
        .order_by()
        .values("visitor_id")
        .annotate(
            deliberate_events=Count("id", filter=Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES)),
            subscriber_events=Count("id", filter=Q(subscriber__isnull=False)),
            js_events=Count("id", filter=Q(js_verified=True)),
        )
        .filter(Q(deliberate_events__gt=0) | Q(subscriber_events__gt=0, js_events__gt=0))
        .values_list("visitor_id", flat=True)
    )


def engaged_visitor_ids(events_qs):
    """The set of engaged-human ``visitor_id``s in ``events_qs``.

    ``events_qs`` is expected to already be human-filtered + period-scoped
    (i.e. ``_base_event_qs``). Both conditions are judged across the visitor's
    events in that queryset, so a subscriber's JS beacon may come from a later
    page than the subscriber-tagged click.
    """
    return set(_engaged_visitor_qs(events_qs))


def _engaged_human_count(events_qs):
    """Distinct engaged-human visitors in ``events_qs`` (see :func:`engaged_visitor_ids`)."""
    return _engaged_visitor_qs(events_qs).count()


DEFAULT_ANALYTICS_DAYS = 90


def _window_timestamps(start_date, end_date):
    start_ts = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
    end_ts = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))
    return start_ts, end_ts


def _preset_start(today, days):
    """First day of a ``days``-long window ending today (inclusive)."""
    return today - datetime.timedelta(days=days - 1)


def default_analytics_window(default_days=DEFAULT_ANALYTICS_DAYS):
    """The window every analytics page opens on: (start_date, end_date, start_ts, end_ts).

    Identical to the matching date preset, so the preset shows as active and the
    warming task's queryset (and therefore its cache key) matches the request's.
    """
    today = timezone.localdate()
    start_date = _preset_start(today, default_days)
    return start_date, today, *_window_timestamps(start_date, today)


def _date_range_from_request(request, default_days=DEFAULT_ANALYTICS_DAYS):
    default_start, today, *_ = default_analytics_window(default_days)
    start_date = _parse_iso_date(request.GET.get("start")) or default_start
    end_date = _parse_iso_date(request.GET.get("end")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date, *_window_timestamps(start_date, end_date)


def _previous_period(start_date, end_date, start_ts):
    """The equal-length period just before the range: (prev_start, prev_end, prev_start_ts, prev_end_ts).

    When the range ends today, the current period has only run until now, so the
    previous period is cut off at the same elapsed time; otherwise a part-day
    would be compared against a whole one. The cut-off is floored to the hour so
    the previous-period queryset (and the visit cache keyed on it) is stable for
    the warming task's interval.
    """
    period_days = (end_date - start_date).days
    prev_end = start_date - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=period_days)
    prev_start_ts, prev_end_ts = _window_timestamps(prev_start, prev_end)
    if end_date == timezone.localdate():
        now = timezone.localtime().replace(minute=0, second=0, microsecond=0)
        prev_end_ts = min(prev_end_ts, prev_start_ts + (now - start_ts))
    return prev_start, prev_end, prev_start_ts, prev_end_ts


def human_events_between(start_ts, end_ts):
    """Human (non-automated) events in a window. The warming task builds the same queryset."""
    return AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)


def _base_event_qs(request, start_ts, end_ts):
    """Return AnalyticsEvent queryset filtered by date, always excluding automated rows."""
    return human_events_between(start_ts, end_ts)


def _render_analytics(request, template, context, panel_template=None):
    start_date = context.get("start_date")
    end_date = context.get("end_date")
    if start_date and end_date:
        today = timezone.localdate()
        presets = []
        for days in (30, 90, 180):
            preset_start = _preset_start(today, days)
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
    return [{"date": timezone.localtime(n["send_date"]).date().isoformat(), "id": n["id"]} for n in newsletters]
