"""
Analytics views for the editorial backend.

Six focused pages, each answering a specific question about how
readers interact with the site.
"""

import datetime
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
        "weekly_trend": weekly_trend,
        "newsletter_sends": newsletter_sends,
        "newsletter_lift": newsletter_lift,
        "human_event_count": human_event_count,
        "subscriber_events": subscriber_events,
        "subscriber_share": _safe_percentage(subscriber_events, human_event_count),
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
        "review_type_breakdown": [
            _group_summary("Featured reviews", featured_rows),
            _group_summary("Standard reviews", standard_rows),
        ],
        "share_breakdown": share_breakdown,
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

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "referrer_breakdown": referrer_breakdown,
        "new_visitors": new_count,
        "returning_visitors": returning_count,
        "total_visitors": len(visitor_ids_in_period),
        "returning_rate": _safe_percentage(returning_count, len(visitor_ids_in_period)),
        "page_breakdown": page_breakdown,
        "journal_visits": journal_visits,
        "active_tab": "traffic",
    }
    return _render_analytics(
        request, "backend/analytics/traffic.html", context, "backend/analytics/_traffic_panel.html"
    )


@login_required
@permission_required(VIEW_NEWSLETTER_STATS, raise_exception=True)
def analytics_email(request):
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request, default_days=180)

    newsletters = list(Newsletter.objects.filter(is_sent=True).order_by("-send_date"))

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
            }
        )

    total_subscribers = Subscriber.objects.filter(subscribed=True).count()
    total_sent = Newsletter.objects.filter(is_sent=True).count()

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "newsletter_rows": newsletter_rows,
        "total_subscribers": total_subscribers,
        "total_sent": total_sent,
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
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)

    total_visits = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT).count()

    article_interactions = human_events.filter(event_type=AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT)
    total_interactions = article_interactions.count()

    action_counter = Counter()
    for meta in article_interactions.values_list("metadata", flat=True):
        action = (meta or {}).get("action", "unknown")
        action_counter[action] += 1

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_visits": total_visits,
        "total_interactions": total_interactions,
        "interaction_breakdown": [{"label": label, "count": count} for label, count in action_counter.most_common()],
        "active_tab": "journals",
    }
    return _render_analytics(
        request, "backend/analytics/journals.html", context, "backend/analytics/_journals_panel.html"
    )
