"""The Newsletter panel: sends, opens, clicks and subscriber acquisition."""

import datetime
import json

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.backend.views.analytics_page import _site_analytics_rollout_date
from spanza_journal_watch.newsletter.models import Newsletter, NewsletterDelivery, Subscriber

from .common import VIEW_NEWSLETTER_STATS, _date_range_from_request, _render_analytics
from .overview import _newsletter_lift_row, _newsletter_lift_windows
from .rates import newsletter_rates

SEGMENT_NEWSLETTER_COUNT = 10


def _acquisition_summary(start_ts, end_ts):
    """Counts of newly-created subscribers in window, grouped by source."""
    qs = Subscriber.objects.filter(created__gte=start_ts, created__lte=end_ts)
    totals = dict(qs.values_list("source").annotate(c=Count("id")).values_list("source", "c"))
    by_source = [
        {"source": value, "label": label, "count": totals.get(value, 0)} for value, label in Subscriber.Source.choices
    ]
    total = sum(row["count"] for row in by_source)
    # A bounce also clears ``subscribed``, but it is a delivery failure rather than a reader's choice.
    unsubscribed = (
        Subscriber.objects.filter(unsubscribed_at__gte=start_ts, unsubscribed_at__lte=end_ts, bounced=False)
        .exclude(tester=True)
        .count()
    )
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


def sendable_subscribers():
    """The subscribers a send would reach now, less testers: the "active subscribers" figure."""
    return Subscriber.get_valid_subscribers(test_email=False).exclude(tester=True)


def _subscriber_segments(newsletters):
    """Engagement segments, scoring each subscriber only on the newsletters they received.

    Receipt comes from the delivery ledger where a send has one; sends from before the
    ledger fall back to "subscribed by the send date". A real click counts as an open.
    """
    segment_counts = {"highly_engaged": 0, "occasional": 0, "dormant": 0}
    if not newsletters:
        return segment_counts
    nl_ids = [nl.pk for nl in newsletters]
    subscribers = list(sendable_subscribers().values_list("id", "created"))
    sub_ids = [sub_id for sub_id, _ in subscribers]

    delivered = set(
        NewsletterDelivery.objects.filter(newsletter_id__in=nl_ids, subscriber_id__in=sub_ids).values_list(
            "newsletter_id", "subscriber_id"
        )
    )
    ledger_newsletters = {nl_id for nl_id, _ in delivered}
    opened = set(
        NewsletterOpen.objects.filter(newsletter_id__in=nl_ids, automated=False)
        .values_list("newsletter_id", "subscriber_id")
        .distinct()
    )
    clicked = set(
        NewsletterClick.objects.filter(newsletter_id__in=nl_ids, automated=False)
        .values_list("newsletter_id", "subscriber_id")
        .distinct()
    )

    for sub_id, created in subscribers:
        received = [
            nl.pk
            for nl in newsletters
            if ((nl.pk, sub_id) in delivered if nl.pk in ledger_newsletters else created <= nl.send_date)
        ]
        if not received:
            continue
        clicks = sum(1 for nl_id in received if (nl_id, sub_id) in clicked)
        opens = sum(1 for nl_id in received if (nl_id, sub_id) in opened or (nl_id, sub_id) in clicked)
        open_rate = opens / len(received)
        click_rate = clicks / len(received)
        if open_rate >= 0.7 and click_rate >= 0.3:
            segment_counts["highly_engaged"] += 1
        elif open_rate >= 0.3 or clicks >= 1:
            segment_counts["occasional"] += 1
        else:
            segment_counts["dormant"] += 1
    return segment_counts


def _newsletter_lift(newsletter, rollout_date):
    """The Overview's lift row, plus the after-week's last day for the in-progress note."""
    row = _newsletter_lift_row(newsletter, rollout_date)
    # The after-week ends at local midnight, so its last whole day is the day before.
    after_end = _newsletter_lift_windows(newsletter)[2]
    row["window_last_day"] = timezone.localtime(after_end).date() - datetime.timedelta(days=1)
    return row


def _newsletter_referred_visitors(newsletter):
    """Distinct human visitors who arrived from a newsletter link in the after-week of the lift window."""
    return (
        AnalyticsEvent.objects.filter(
            automated=False,
            referrer_category="newsletter",
            visitor_id__isnull=False,
            timestamp__gte=newsletter.send_date,
            timestamp__lt=_newsletter_lift_windows(newsletter)[2],
        )
        .values("visitor_id")
        .distinct()
        .count()
    )


@login_required
@permission_required(VIEW_NEWSLETTER_STATS, raise_exception=True)
def analytics_email(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request, default_days=180)
    site_analytics_rollout_date = _site_analytics_rollout_date()
    now = timezone.now()

    newsletters = list(
        Newsletter.objects.filter(
            is_sent=True,
            send_date__date__gte=start_date,
            send_date__date__lte=end_date,
        ).order_by("-send_date")
    )
    rates_by_nl = newsletter_rates(newsletters)
    newsletter_lift = [_newsletter_lift(nl, site_analytics_rollout_date) for nl in newsletters if nl.send_date]
    partial_ids = {row["newsletter"].pk for row in newsletter_lift if row["site_analytics_partial"]}

    newsletter_rows = []
    for nl in newsletters:
        rates = rates_by_nl[nl.pk]
        site_analytics_partial = nl.pk in partial_ids
        post_traffic = None
        if nl.send_date and not site_analytics_partial:
            post_traffic = _newsletter_referred_visitors(nl)
        newsletter_rows.append(
            {
                "newsletter": nl,
                "rates": rates,
                "post_send_visitors": post_traffic,
                "post_send_in_progress": bool(nl.send_date and now < _newsletter_lift_windows(nl)[2]),
                "link_clicks": rates.link_clicks,
                "site_analytics_partial": site_analytics_partial,
            }
        )

    total_subscribers = sendable_subscribers().count()
    total_sent = Newsletter.objects.filter(is_sent=True).count()

    # Subscriber engagement segmentation (always uses most recent sends, not date-filtered)
    recent_newsletters = list(
        Newsletter.objects.filter(is_sent=True, send_date__isnull=False).order_by("-send_date")[
            :SEGMENT_NEWSLETTER_COUNT
        ]
    )
    segment_counts = _subscriber_segments(recent_newsletters)

    # Trend chart data — serialise for Chart.js, oldest send first.
    trend_rows = [row for row in reversed(newsletter_rows) if row["newsletter"].send_date]
    trend_labels = json.dumps(
        [timezone.localtime(row["newsletter"].send_date).strftime("%-d %b %Y") for row in trend_rows]
    )
    trend_open_rates = json.dumps([round(row["rates"].open_rate_value * 100) for row in trend_rows])
    trend_ctrs = json.dumps([round(row["rates"].click_rate_value * 100) for row in trend_rows])

    acquisition = _acquisition_summary(start_ts, end_ts)
    recent_feed = _recent_subscriber_feed(start_ts, end_ts, limit=20)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "newsletter_rows": newsletter_rows,
        "total_subscribers": total_subscribers,
        "total_sent": total_sent,
        "segment_counts": segment_counts,
        "segment_newsletter_count": len(recent_newsletters),
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
