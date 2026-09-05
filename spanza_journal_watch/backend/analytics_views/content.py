"""The Content panel and content search."""

from collections import defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
)
from spanza_journal_watch.submissions.models import Issue, Review

from .common import VIEW_SITE_ANALYTICS, _base_event_qs, _date_range_from_request
from .issues import _ISSUE_SHARE_EVENT_TYPES


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_content(request):
    """Legacy URL — redirects to editorial."""
    qs = request.GET.urlencode()
    url = reverse("backend:analytics_editorial")
    return redirect(f"{url}?{qs}" if qs else url)


_CONTENT_SEARCH_LIMIT = 20


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_content_search(request):
    """HTMX fragment: find any review or issue by name and show its window stats.

    Searches the full catalogue (not just the top-N leaderboards), so an admin can
    jump straight to a specific piece of content. ``kind`` narrows to review/issue;
    default searches both.
    """
    query = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    if not query:
        return render(
            request,
            "backend/analytics/_content_search_results.html",
            {"q": query, "reviews": [], "issues": [], "empty_query": True},
        )

    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_ct)
    E = AnalyticsEvent.EventType

    review_results = []
    reviews_truncated = False
    if kind != "issue":
        matched = list(
            Review.objects.filter(active=True)
            .filter(
                Q(article__title__icontains=query)
                | Q(article__journal__name__icontains=query)
                | Q(author__name__icontains=query)
            )
            .select_related("article__journal", "author")
            .defer("body", "search_vector", "article__abstract", "article__metadata_json", "article__tags_string")
            .order_by("-publish_date", "-id")[: _CONTENT_SEARCH_LIMIT + 1]
        )
        reviews_truncated = len(matched) > _CONTENT_SEARCH_LIMIT
        matched = matched[:_CONTENT_SEARCH_LIMIT]
        stats = {
            row["object_id"]: row
            for row in review_events.filter(object_id__in=[r.id for r in matched])
            .values("object_id")
            .annotate(
                opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
                engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
                full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
                shares=Count("id", filter=Q(event_type__in=_ISSUE_SHARE_EVENT_TYPES)),
            )
        }
        for review in matched:
            s = stats.get(review.id, {})
            opens = s.get("opens", 0)
            review_results.append(
                {
                    "review": review,
                    "opens": opens,
                    "engaged": s.get("engaged", 0),
                    "engaged_rate": _safe_percentage(s.get("engaged", 0), opens),
                    "full_text": s.get("full_text", 0),
                    "shares": s.get("shares", 0),
                }
            )

    issue_results = []
    issues_truncated = False
    if kind != "review":
        matched_issues = list(
            Issue.objects.filter(active=True)
            .filter(Q(name__icontains=query) | Q(slug__icontains=query))
            .order_by("-date", "-id")[: _CONTENT_SEARCH_LIMIT + 1]
        )
        issues_truncated = len(matched_issues) > _CONTENT_SEARCH_LIMIT
        matched_issues = matched_issues[:_CONTENT_SEARCH_LIMIT]
        issue_ids = [i.id for i in matched_issues]
        issue_reviews = defaultdict(list)
        for issue_id, rid in Issue.reviews.through.objects.filter(issue_id__in=issue_ids).values_list(
            "issue_id", "review_id"
        ):
            issue_reviews[issue_id].append(rid)
        all_rids = {rid for rids in issue_reviews.values() for rid in rids}
        review_stats = {
            row["object_id"]: row
            for row in review_events.filter(object_id__in=all_rids)
            .values("object_id")
            .annotate(
                opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
                engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            )
        }
        for issue in matched_issues:
            rids = issue_reviews.get(issue.id, [])
            opens = sum(review_stats.get(rid, {}).get("opens", 0) for rid in rids)
            engaged = sum(review_stats.get(rid, {}).get("engaged", 0) for rid in rids)
            issue_results.append(
                {
                    "issue": issue,
                    "review_count": len(rids),
                    "opens": opens,
                    "engaged": engaged,
                    "engaged_rate": _safe_percentage(engaged, opens),
                }
            )

    return render(
        request,
        "backend/analytics/_content_search_results.html",
        {
            "q": query,
            "reviews": review_results,
            "issues": issue_results,
            "reviews_truncated": reviews_truncated,
            "issues_truncated": issues_truncated,
            "limit": _CONTENT_SEARCH_LIMIT,
            "empty_query": False,
        },
    )
