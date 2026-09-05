"""The legacy site analytics page and the helpers the analytics panels share."""

import datetime
import logging
from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.submissions.models import (
    Review,
)

logger = logging.getLogger(__name__)


def _parse_iso_date(value):
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _safe_percentage(numerator, denominator):
    if not denominator:
        return "0%"
    return f"{round((numerator / denominator) * 100)}%"


def _site_analytics_rollout_date():
    first_js_event = (
        AnalyticsEvent.objects.filter(js_verified=True)
        .order_by("timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    if not first_js_event:
        return None
    if timezone.is_aware(first_js_event):
        return timezone.localtime(first_js_event).date()
    return first_js_event.date()


def _newsletter_predates_site_analytics(newsletter):
    if not newsletter or not newsletter.send_date:
        return False
    rollout_date = _site_analytics_rollout_date()
    if rollout_date is None:
        return False
    send_date = timezone.localtime(newsletter.send_date).date() if timezone.is_aware(newsletter.send_date) else None
    if send_date is None:
        send_date = newsletter.send_date.date()
    return send_date < rollout_date


def _build_rate_row(*, label, opens=0, engaged=0, shares=0, full_text=0, searches=0, result_clicks=0, score=0):
    actions = shares + full_text
    return {
        "label": label,
        "opens": opens,
        "engaged": engaged,
        "shares": shares,
        "full_text": full_text,
        "actions": actions,
        "searches": searches,
        "result_clicks": result_clicks,
        "engaged_rate": _safe_percentage(engaged, opens),
        "share_rate": _safe_percentage(shares, opens),
        "full_text_ctr": _safe_percentage(full_text, opens),
        "action_rate": _safe_percentage(actions, engaged),
        "search_click_rate": _safe_percentage(result_clicks, searches),
        "score": score,
    }


@login_required
@permission_required("backend.view_newsletter_stats", raise_exception=True)
def site_analytics(request):
    today = timezone.localdate()
    default_start = today - datetime.timedelta(days=90)
    start_date = _parse_iso_date(request.GET.get("start")) or default_start
    end_date = _parse_iso_date(request.GET.get("end")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    start_ts = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
    end_ts = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)
    human_event_count = human_events.count()
    review_content_type = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_content_type)

    share_event_types = [
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    ]
    social_share_event_types = {
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    }

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            avg_dwell_ms=Avg("duration_ms", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
            copy_link_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK)),
            email_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL)),
            native_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE)),
            social_shares=Count("id", filter=Q(event_type__in=social_share_event_types)),
            total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        )
        .order_by("-opens", "-engaged_views", "-full_text_clicks")
    )

    review_ids = [row["object_id"] for row in review_summary_rows if row["object_id"]]
    reviews_by_id = {
        review.id: review
        for review in Review.objects.filter(id__in=review_ids)
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

        engagement_score = (
            row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
        )

        summary = {
            "review": review,
            "opens": row["opens"],
            "engaged_views": row["engaged_views"],
            "avg_dwell_seconds": round((row["avg_dwell_ms"] or 0) / 1000, 1),
            "full_text_clicks": row["full_text_clicks"],
            "copy_link_shares": row["copy_link_shares"],
            "email_shares": row["email_shares"],
            "native_shares": row["native_shares"],
            "social_shares": row["social_shares"],
            "total_shares": row["total_shares"],
            "engagement_score": engagement_score,
            "engaged_rate": _safe_percentage(row["engaged_views"], row["opens"]),
            "share_rate": _safe_percentage(row["total_shares"], row["opens"]),
            "full_text_ctr": _safe_percentage(row["full_text_clicks"], row["opens"]),
            "action_rate": _safe_percentage(row["total_shares"] + row["full_text_clicks"], row["engaged_views"]),
            "is_featured": review.is_featured,
        }
        top_reviews.append(summary)

        journal = review.article.journal.name if review.article.journal else "Unknown journal"
        journal_totals[journal]["opens"] += row["opens"]
        journal_totals[journal]["engaged"] += row["engaged_views"]
        journal_totals[journal]["shares"] += row["total_shares"]
        journal_totals[journal]["full_text"] += row["full_text_clicks"]
        journal_totals[journal]["score"] += engagement_score

        for tag in review.article.tags.all():
            label = str(tag)
            tag_totals[label]["opens"] += row["opens"]
            tag_totals[label]["engaged"] += row["engaged_views"]
            tag_totals[label]["shares"] += row["total_shares"]
            tag_totals[label]["full_text"] += row["full_text_clicks"]
            tag_totals[label]["score"] += engagement_score

    top_reviews = sorted(top_reviews, key=lambda item: item["engagement_score"], reverse=True)[:12]
    top_journals = sorted(
        ({"label": label, **totals} for label, totals in journal_totals.items()),
        key=lambda item: item["score"],
        reverse=True,
    )[:10]
    top_tags = sorted(
        ({"label": label, **totals} for label, totals in tag_totals.items()),
        key=lambda item: item["score"],
        reverse=True,
    )[:12]

    total_review_opens = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN).count()
    total_engaged_views = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).count()
    total_full_text_clicks = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK).count()
    total_shares = review_events.filter(event_type__in=share_event_types).count()
    average_engaged_ms = (
        review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).aggregate(avg=Avg("duration_ms"))[
            "avg"
        ]
        or 0
    )

    share_counts = {
        "Copy link": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK).count(),
        "Email": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL).count(),
        "Native share": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE).count(),
        "Bluesky": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY).count(),
        "X": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_X).count(),
        "Facebook": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK).count(),
    }
    share_breakdown = [{"label": label, "count": count} for label, count in share_counts.items() if count]

    confidence_rows = list(
        human_events.values("human_confidence")
        .annotate(
            events=Count("id"),
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            shares=Count("id", filter=Q(event_type__in=share_event_types)),
            full_text=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
        )
        .order_by("-events")
    )
    confidence_labels = {
        AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN: "Known subscriber human",
        AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN: "Probable human",
        AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED: "Suspected automated",
    }
    confidence_breakdown = [
        {
            "label": confidence_labels.get(row["human_confidence"], row["human_confidence"] or "Unknown"),
            "events": row["events"],
            "opens": row["opens"],
            "engaged": row["engaged"],
            "shares": row["shares"],
            "full_text": row["full_text"],
            "share_of_events": _safe_percentage(row["events"], human_event_count),
        }
        for row in confidence_rows
    ]
    known_subscriber_events = next(
        (
            row["events"]
            for row in confidence_rows
            if row["human_confidence"] == AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN
        ),
        0,
    )
    probable_human_events = next(
        (
            row["events"]
            for row in confidence_rows
            if row["human_confidence"] == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN
        ),
        0,
    )

    source_rows = list(
        review_events.values("source")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            shares=Count("id", filter=Q(event_type__in=share_event_types)),
            full_text=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
        )
        .order_by("-opens", "-engaged")
    )
    source_labels = {
        "home-review-modal": "Homepage modal",
        "review_modal": "Review modal",
        "review_detail": "Review detail page",
        "issue_detail": "Issue detail page",
        "author-review-modal": "Author modal",
        "tag-review-modal": "Tag modal",
        "search_results": "Search results",
        "search_page": "Search page",
    }
    source_breakdown = [
        _build_rate_row(
            label=source_labels.get(row["source"], row["source"] or "Unknown"),
            opens=row["opens"],
            engaged=row["engaged"],
            shares=row["shares"],
            full_text=row["full_text"],
            score=(row["opens"] + (row["engaged"] * 3) + (row["full_text"] * 4) + (row["shares"] * 5)),
        )
        for row in source_rows
    ]

    featured_ids = {review.id for review in reviews_by_id.values() if review.is_featured}
    featured_review_rows = [row for row in review_summary_rows if row["object_id"] in featured_ids]
    standard_review_rows = [row for row in review_summary_rows if row["object_id"] not in featured_ids]

    def _summarize_review_group(label, rows):
        opens = sum(row["opens"] for row in rows)
        engaged = sum(row["engaged_views"] for row in rows)
        shares = sum(row["total_shares"] for row in rows)
        full_text = sum(row["full_text_clicks"] for row in rows)
        score = sum(
            row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
            for row in rows
        )
        return _build_rate_row(
            label=label,
            opens=opens,
            engaged=engaged,
            shares=shares,
            full_text=full_text,
            score=score,
        )

    review_type_breakdown = [
        _summarize_review_group("Featured reviews", featured_review_rows),
        _summarize_review_group("Standard reviews", standard_review_rows),
    ]

    search_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
    search_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK)
    search_query_counter = Counter()
    search_result_counter = Counter()
    zero_result_counter = Counter()
    for metadata in search_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1
    for metadata in search_click_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        search_result_counter[label] += 1

    search_insights = []
    for label, count in search_query_counter.most_common(10):
        click_count = search_result_counter.get(label, 0)
        search_insights.append(
            {
                "label": label,
                "searches": count,
                "result_clicks": click_count,
                "click_through_rate": _safe_percentage(click_count, count),
                "zero_results": zero_result_counter.get(label, 0),
            }
        )

    content_opportunities = []
    for item in sorted(top_reviews, key=lambda row: (-row["opens"], row["engaged_views"]))[:6]:
        if item["opens"] >= 1 and item["engaged_views"] < item["opens"]:
            content_opportunities.append(
                {
                    "review": item["review"],
                    "signal": "High opens, weaker engagement",
                    "detail": f"{item['engaged_rate']} engaged, {item['share_rate']} shared",
                }
            )
    for item in sorted(top_reviews, key=lambda row: (-row["engagement_score"], -row["total_shares"]))[:6]:
        if item["engagement_score"] and item["total_shares"]:
            content_opportunities.append(
                {
                    "review": item["review"],
                    "signal": "Strong engagement and sharing",
                    "detail": f"Score {item['engagement_score']}, {item['total_shares']} shares",
                }
            )
    content_opportunities = content_opportunities[:8]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_review_opens": total_review_opens,
        "total_engaged_views": total_engaged_views,
        "engaged_rate": _safe_percentage(total_engaged_views, total_review_opens),
        "total_full_text_clicks": total_full_text_clicks,
        "full_text_ctr": _safe_percentage(total_full_text_clicks, total_review_opens),
        "total_shares": total_shares,
        "share_rate": _safe_percentage(total_shares, total_review_opens),
        "average_dwell_seconds": round(average_engaged_ms / 1000, 1),
        "search_count": search_events.count(),
        "search_result_click_count": search_click_events.count(),
        "search_click_rate": _safe_percentage(search_click_events.count(), search_events.count()),
        "share_breakdown": share_breakdown,
        "confidence_breakdown": confidence_breakdown,
        "known_subscriber_events": known_subscriber_events,
        "known_subscriber_share": _safe_percentage(known_subscriber_events, human_event_count),
        "probable_human_events": probable_human_events,
        "probable_human_share": _safe_percentage(probable_human_events, human_event_count),
        "source_breakdown": source_breakdown,
        "review_type_breakdown": review_type_breakdown,
        "top_reviews": top_reviews,
        "top_journals": top_journals,
        "top_tags": top_tags,
        "search_insights": search_insights,
        "content_opportunities": content_opportunities,
    }
    return render(request, "backend/site_analytics.html", context)


@login_required
def analytics_redirect(request):
    return HttpResponseRedirect(reverse("backend:analytics_overview"))
