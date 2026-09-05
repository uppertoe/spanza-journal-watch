"""The Editorial panel: what to cover next (the Search tab redirects here)."""

from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _build_rate_row,
    _safe_percentage,
)
from spanza_journal_watch.submissions.models import Review

from .benchmarks import (
    _benchmark_verdict,
    _confidence_summary,
    _first_window_benchmark,
    _first_window_opens,
    _review_publish_date,
)
from .common import VIEW_SITE_ANALYTICS, _base_event_qs, _date_range_from_request, _render_analytics, _weekly_buckets
from .visits import _LOW_SAMPLE_THRESHOLD, _normalise_search_query, _rank_rows


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_editorial(request):
    """Editorial Intelligence — merges content engagement + search data."""
    start_date, end_date, start_ts, end_ts = _date_range_from_request(request)

    human_events = _base_event_qs(request, start_ts, end_ts)
    review_ct = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_ct)

    E = AnalyticsEvent.EventType
    share_event_types = [
        E.REVIEW_SHARE_COPY_LINK,
        E.REVIEW_SHARE_EMAIL,
        E.REVIEW_SHARE_NATIVE,
        E.REVIEW_SHARE_BLUESKY,
        E.REVIEW_SHARE_X,
        E.REVIEW_SHARE_FACEBOOK,
    ]
    editorial_agg = review_events.aggregate(
        total_opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
        total_engaged=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
        total_full_text=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
        total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
    )
    total_opens = editorial_agg["total_opens"]
    total_engaged = editorial_agg["total_engaged"]
    total_full_text = editorial_agg["total_full_text"]
    total_shares = editorial_agg["total_shares"]

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=E.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=E.REVIEW_ENGAGED)),
            avg_dwell_ms=Avg("duration_ms", filter=Q(event_type=E.REVIEW_ENGAGED)),
            avg_scroll=Avg("scroll_depth", filter=Q(event_type=E.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=E.REVIEW_FULL_TEXT_CLICK)),
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
        .defer("body", "search_vector", "article__abstract", "article__metadata_json", "article__tags_string")
    }

    top_reviews = []
    journal_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})
    tag_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})

    for row in review_summary_rows:
        review = reviews_by_id.get(row["object_id"])
        if not review:
            continue
        score = row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
        engaged_rate_value = (row["engaged_views"] / row["opens"]) if row["opens"] else 0
        share_rate_value = (row["total_shares"] / row["opens"]) if row["opens"] else 0
        full_text_ctr_value = (row["full_text_clicks"] / row["opens"]) if row["opens"] else 0
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
                "engaged_rate_value": engaged_rate_value,
                "share_rate_value": share_rate_value,
                "full_text_ctr_value": full_text_ctr_value,
                "avg_scroll_depth": round(row["avg_scroll"]) if row["avg_scroll"] is not None else None,
                "low_sample": row["opens"] < _LOW_SAMPLE_THRESHOLD,
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

    # Engagement velocity — score per week since publication. Uses the issue-date
    # fallback so reviews without their own publish_date still get a velocity.
    today = timezone.localdate()
    for item in top_reviews:
        review = item["review"]
        publish = _review_publish_date(review)
        item["publish_date"] = publish
        if publish:
            days_since = max((today - publish).days, 1)
            weeks_since = max(days_since / 7, 0.5)
            item["velocity"] = round(item["engagement_score"] / weeks_since, 1)
            item["weeks_since"] = round(weeks_since, 1)
        else:
            item["velocity"] = None
            item["weeks_since"] = None

    top_reviews_by_reach = _rank_rows(top_reviews, ("opens", "engaged_views", "full_text_clicks"))
    top_reviews_by_depth = _rank_rows(top_reviews, ("engaged_views", "avg_dwell_seconds", "avg_scroll_depth"))
    top_reviews_by_full_text = _rank_rows(top_reviews, ("full_text_clicks", "full_text_ctr_value", "opens"))
    top_reviews_by_shares = _rank_rows(top_reviews, ("total_shares", "share_rate_value", "opens"))
    top_reviews_by_velocity = _rank_rows(
        [item for item in top_reviews if item["velocity"] is not None],
        ("velocity", "opens"),
    )

    # ── New releases — reviews published within the window, judged on their own
    # terms (velocity + first-week opens vs the cohort median) so freshly-released
    # content surfaces instead of being buried under the accumulated archive.
    benchmark_median, benchmark_cohort = _first_window_benchmark(review_ct, today)
    new_releases = []
    for item in top_reviews:
        publish = item["publish_date"]
        if not publish or not (start_date <= publish <= end_date):
            continue
        review = item["review"]
        first_week_opens = _first_window_opens(review, review_ct, today)
        verdict = _benchmark_verdict(first_week_opens, benchmark_median)
        new_releases.append(
            {
                **item,
                "first_week_opens": first_week_opens,
                "benchmark_verdict": verdict,
                "days_live": max((today - publish).days, 0),
                "window_pending": first_week_opens is None,
            }
        )
    new_releases.sort(key=lambda r: (r["publish_date"], r["velocity"] or 0), reverse=True)
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

    editorial_share_map = dict(
        review_events.filter(event_type__in=share_event_types)
        .values_list("event_type")
        .annotate(count=Count("id"))
        .values_list("event_type", "count")
    )
    share_counts = {
        "Copy link": editorial_share_map.get(E.REVIEW_SHARE_COPY_LINK, 0),
        "Email": editorial_share_map.get(E.REVIEW_SHARE_EMAIL, 0),
        "Native share": editorial_share_map.get(E.REVIEW_SHARE_NATIVE, 0),
        "Bluesky": editorial_share_map.get(E.REVIEW_SHARE_BLUESKY, 0),
        "X": editorial_share_map.get(E.REVIEW_SHARE_X, 0),
        "Facebook": editorial_share_map.get(E.REVIEW_SHARE_FACEBOOK, 0),
    }
    share_breakdown = [{"label": k, "count": v} for k, v in share_counts.items() if v]

    # Share-to-visit attribution — count downstream visits from share tokens
    # Visitors who arrived via a share link (carry a ref token) AND actually
    # engaged. Excludes link-preview/unfurl fetchers, which land a single
    # tokened page_visit with no interaction and would otherwise inflate this.
    share_attributed_visits = (
        human_events.exclude(share_token="")
        .filter(visitor_id__isnull=False)
        .filter(Q(subscriber__isnull=False) | Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES))
        .values("visitor_id")
        .distinct()
        .count()
    )

    # Tag-based content type breakdown — shows what topics resonate
    tag_type_breakdown = sorted(
        (
            {
                "label": k,
                "opens": v["opens"],
                "engaged": v["engaged"],
                "engaged_rate": _safe_percentage(v["engaged"], v["opens"]),
                "full_text": v["full_text"],
                "full_text_ctr": _safe_percentage(v["full_text"], v["opens"]),
                "score": v["score"],
            }
            for k, v in tag_totals.items()
        ),
        key=lambda x: x["score"],
        reverse=True,
    )[:10]

    # ── Search data (merged from analytics_search) ──────────────────
    search_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
    search_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK)

    search_query_counter = Counter()
    zero_result_counter = Counter()
    for metadata in search_events.values_list("metadata", flat=True):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label is None:
            continue
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1

    click_counter = Counter()
    for metadata in search_click_events.values_list("metadata", flat=True):
        label = _normalise_search_query((metadata or {}).get("query"))
        if label is None:
            continue
        click_counter[label] += 1

    browse_searches = search_query_counter.pop("[browse]", 0)
    browse_clicks = click_counter.pop("[browse]", 0)
    zero_result_counter.pop("[browse]", 0)

    real_search_total = sum(search_query_counter.values())
    real_click_total = sum(click_counter.values())

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

    # ── Cross-reference: topics with unmet demand ───────────────────
    # Match zero-result search terms against tag names (case-insensitive)
    tag_names_lower = {}
    for label in tag_totals:
        tag_names_lower[label.lower()] = label
        tag_names_lower[label.lower().lstrip("#")] = label
    unmet_demand = []
    for zq in zero_result_queries[:10]:
        q_lower = zq["label"].lower()
        matched_tag = tag_names_lower.get(q_lower)
        if matched_tag:
            tag_data = tag_totals[matched_tag]
            unmet_demand.append(
                {
                    "query": zq["label"],
                    "searches": zq["searches"],
                    "zero_results": zq["zero_results"],
                    "tag_score": tag_data["score"],
                }
            )

    # ── Archive discovery via "Related reviews" clicks ──────────────────
    related_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_RELATED_CLICK)
    related_clicks_total = related_click_events.count()
    related_by_surface = Counter()
    for source in related_click_events.values_list("source", flat=True):
        related_by_surface[source or "unknown"] += 1
    _SURFACE_LABELS = {
        "review_detail": "Review page",
        "card_modal": "Home/card modal",
        "issue": "Issue page",
        "journal_browser": "Journal browser",
        "unknown": "Unknown",
    }
    related_surface_breakdown = sorted(
        ({"label": _SURFACE_LABELS.get(s, s), "count": c} for s, c in related_by_surface.items()),
        key=lambda x: -x["count"],
    )
    # Top archive destinations reached via a Related click.
    related_dest_rows = list(
        related_click_events.filter(content_type=review_ct)
        .values("object_id")
        .annotate(clicks=Count("id"))
        .order_by("-clicks")[:10]
    )
    related_dest_ids = [r["object_id"] for r in related_dest_rows if r["object_id"]]
    related_dest_reviews = {
        r.id: r for r in Review.objects.filter(id__in=related_dest_ids).select_related("article__journal")
    }
    top_related_destinations = [
        {"review": related_dest_reviews[r["object_id"]], "clicks": r["clicks"]}
        for r in related_dest_rows
        if r["object_id"] in related_dest_reviews
    ]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        # Content engagement
        "total_opens": total_opens,
        "total_engaged": total_engaged,
        "total_full_text": total_full_text,
        "total_shares": total_shares,
        "engaged_rate": _safe_percentage(total_engaged, total_opens),
        "full_text_ctr": _safe_percentage(total_full_text, total_opens),
        "share_rate": _safe_percentage(total_shares, total_opens),
        "new_releases": new_releases,
        "benchmark_median": benchmark_median,
        "benchmark_cohort": benchmark_cohort,
        "benchmark_ready": benchmark_median is not None,
        "top_reviews_by_velocity": top_reviews_by_velocity,
        "top_reviews_by_reach": top_reviews_by_reach,
        "top_reviews_by_depth": top_reviews_by_depth,
        "top_reviews_by_full_text": top_reviews_by_full_text,
        "top_reviews_by_shares": top_reviews_by_shares,
        "top_journals": top_journals,
        "top_tags": top_tags,
        "tag_type_breakdown": tag_type_breakdown,
        "review_type_breakdown": [
            _group_summary("Featured reviews", featured_rows),
            _group_summary("Standard reviews", standard_rows),
        ],
        "share_breakdown": share_breakdown,
        "share_attributed_visits": share_attributed_visits,
        "share_attributed_visit_rate": _safe_percentage(share_attributed_visits, total_shares),
        # Search data
        "total_searches": real_search_total,
        "search_click_count": real_click_total,
        "search_ctr": _safe_percentage(real_click_total, real_search_total),
        "browse_searches": browse_searches,
        "browse_ctr": _safe_percentage(browse_clicks, browse_searches),
        "search_insights": search_insights,
        "zero_result_queries": zero_result_queries[:10],
        "weekly_searches": weekly_searches,
        "unmet_demand": unmet_demand,
        # Archive discovery
        "related_clicks_total": related_clicks_total,
        "related_surface_breakdown": related_surface_breakdown,
        "top_related_destinations": top_related_destinations,
        "active_tab": "content",
    }
    context.update(_confidence_summary(human_events))
    return _render_analytics(
        request, "backend/analytics/content.html", context, "backend/analytics/_content_panel.html"
    )


@login_required
@permission_required(VIEW_SITE_ANALYTICS, raise_exception=True)
def analytics_search(request):
    """Legacy URL — redirects to editorial."""
    qs = request.GET.urlencode()
    url = reverse("backend:analytics_editorial")
    return redirect(f"{url}?{qs}" if qs else url)
