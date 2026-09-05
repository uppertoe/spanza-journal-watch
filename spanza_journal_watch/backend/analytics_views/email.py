"""The Newsletter panel: sends, opens, clicks and subscriber acquisition."""

import datetime
import json

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.backend.views.analytics_page import (
    _newsletter_predates_site_analytics,
    _safe_percentage,
    _site_analytics_rollout_date,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber

from .common import VIEW_NEWSLETTER_STATS, _date_range_from_request, _render_analytics


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
