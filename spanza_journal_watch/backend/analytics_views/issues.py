"""Issue-level analytics."""

from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
)
from spanza_journal_watch.submissions.models import Issue, Review

from .benchmarks import _confidence_summary, _referrer_source_breakdown
from .common import VIEW_SITE_ANALYTICS, _base_event_qs, _date_range_from_request, _render_analytics, _weekly_buckets
from .visits import _LOW_SAMPLE_THRESHOLD, _rank_rows

_ISSUE_SHARE_EVENT_TYPES = [
    AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
    AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
    AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
    AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
    AnalyticsEvent.EventType.REVIEW_SHARE_X,
    AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
]


def _issue_page_visits_by_slug(human_events):
    """Count PAGE_VISIT landings on /issues/<slug>, keyed by slug.

    Issue detail is served at /issues/<slug> (no trailing slash), but tolerate a
    trailing slash from clients that add one.
    """
    paths = human_events.filter(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        metadata__path__startswith="/issues/",
    ).values_list("metadata__path", flat=True)
    visits = Counter()
    for path in paths:
        slug = (path or "").split("/issues/", 1)[-1].strip("/")
        # Skip nested paths (e.g. /issues/latest is not a real slug we track here).
        if slug and "/" not in slug:
            visits[slug] += 1
    return visits


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_issues(request):
    """Issue-level engagement leaderboard.

    Reviews are M2M to issues, so a review shared across issues has its events
    counted toward each issue it appears in; issue totals intentionally do not
    sum to a site-wide total. Ordered newest-issue-first so recent issues surface
    rather than being outranked by the accumulated archive.
    """
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)
    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_ct)
    E = AnalyticsEvent.EventType

    review_stats = {
        row["object_id"]: row
        for row in review_events.values("object_id").annotate(
            opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
            shares=Count("id", filter=Q(event_type__in=_ISSUE_SHARE_EVENT_TYPES)),
        )
    }

    issues = list(Issue.objects.filter(active=True).order_by("-date", "-id"))
    issue_ids = [i.id for i in issues]
    issue_reviews = defaultdict(list)
    for issue_id, review_id in Issue.reviews.through.objects.filter(issue_id__in=issue_ids).values_list(
        "issue_id", "review_id"
    ):
        issue_reviews[issue_id].append(review_id)

    visits_by_slug = _issue_page_visits_by_slug(human_events)

    today = timezone.localdate()
    issue_rows = []
    for issue in issues:
        rids = issue_reviews.get(issue.id, [])
        opens = sum(review_stats.get(rid, {}).get("opens", 0) for rid in rids)
        engaged = sum(review_stats.get(rid, {}).get("engaged", 0) for rid in rids)
        full_text = sum(review_stats.get(rid, {}).get("full_text", 0) for rid in rids)
        shares = sum(review_stats.get(rid, {}).get("shares", 0) for rid in rids)
        page_views = visits_by_slug.get(issue.slug, 0)
        weeks_since = None
        if issue.date:
            weeks_since = round(max((today - issue.date).days, 1) / 7, 1)
        issue_rows.append(
            {
                "issue": issue,
                "review_count": len(rids),
                "opens": opens,
                "engaged": engaged,
                "engaged_rate": _safe_percentage(engaged, opens),
                "full_text": full_text,
                "shares": shares,
                "page_views": page_views,
                "weeks_since": weeks_since,
                "low_sample": opens < _LOW_SAMPLE_THRESHOLD,
            }
        )

    tracked_issue_count = len(issue_rows)
    most_engaged_issues = _rank_rows(issue_rows, ("opens", "engaged", "shares"), limit=5)
    any_engaged_issues = any(r["opens"] for r in most_engaged_issues)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "issue_rows": issue_rows,
        "most_engaged_issues": most_engaged_issues,
        "any_engaged_issues": any_engaged_issues,
        "tracked_issue_count": tracked_issue_count,
        "active_tab": "issues",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(request, "backend/analytics/issues.html", context, "backend/analytics/_issues_panel.html")


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_issue_detail(request, issue_pk):
    """Per-issue drill-down: funnel, reviews within the issue, views over time,
    and how readers reached the issue."""
    issue = Issue.objects.filter(pk=issue_pk).first()
    if issue is None:
        return redirect("backend:analytics_issues")

    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)
    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    E = AnalyticsEvent.EventType

    review_ids = list(issue.reviews.values_list("id", flat=True))
    issue_review_events = human_events.filter(content_type=review_ct, object_id__in=review_ids)

    funnel = issue_review_events.aggregate(
        total_opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
        total_engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
        total_full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
        total_shares=Count("id", filter=Q(event_type__in=_ISSUE_SHARE_EVENT_TYPES)),
    )
    total_opens = funnel["total_opens"]
    total_engaged = funnel["total_engaged"]
    total_full_text = funnel["total_full_text"]
    total_shares = funnel["total_shares"]

    per_review = {
        row["object_id"]: row
        for row in issue_review_events.values("object_id").annotate(
            opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
            shares=Count("id", filter=Q(event_type__in=_ISSUE_SHARE_EVENT_TYPES)),
        )
    }
    reviews_by_id = {
        r.id: r
        for r in issue.reviews.select_related("article__journal", "author").defer(
            "body", "search_vector", "article__abstract", "article__metadata_json", "article__tags_string"
        )
    }
    review_rows = []
    for rid, review in reviews_by_id.items():
        stats = per_review.get(rid, {})
        opens = stats.get("opens", 0)
        review_rows.append(
            {
                "review": review,
                "opens": opens,
                "engaged": stats.get("engaged", 0),
                "engaged_rate": _safe_percentage(stats.get("engaged", 0), opens),
                "full_text": stats.get("full_text", 0),
                "shares": stats.get("shares", 0),
                "low_sample": opens < _LOW_SAMPLE_THRESHOLD,
            }
        )
    review_rows.sort(key=lambda r: (r["opens"], r["engaged"], r["shares"]), reverse=True)

    open_events = issue_review_events.filter(event_type=E.REVIEW_OPEN)
    weekly_opens = _weekly_buckets(open_events, weeks=26)
    referrer_breakdown = _referrer_source_breakdown(open_events)

    page_view_count = _issue_page_visits_by_slug(human_events).get(issue.slug, 0)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "issue": issue,
        "review_count": len(review_ids),
        "page_view_count": page_view_count,
        "total_opens": total_opens,
        "total_engaged": total_engaged,
        "total_full_text": total_full_text,
        "total_shares": total_shares,
        "engaged_rate": _safe_percentage(total_engaged, total_opens),
        "full_text_ctr": _safe_percentage(total_full_text, total_opens),
        "share_rate": _safe_percentage(total_shares, total_opens),
        "review_rows": review_rows,
        "weekly_opens": weekly_opens,
        "referrer_breakdown": referrer_breakdown,
        "active_tab": "issues",
    }
    return render(request, "backend/analytics/issue_detail.html", context)
