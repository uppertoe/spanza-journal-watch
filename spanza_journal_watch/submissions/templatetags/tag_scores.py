"""
Engagement-based tag scoring for the Explore page.

Aggregates AnalyticsEvent data (review opens, engaged views, full-text clicks,
shares) into per-tag scores, cached for 6 hours.
"""

from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.submissions.models import Review

SHARE_EVENT_TYPES = [
    AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
    AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
    AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
    AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
    AnalyticsEvent.EventType.REVIEW_SHARE_X,
    AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
]

CACHE_KEY = "tag_engagement_scores"
CACHE_TIMEOUT = 60 * 60 * 6  # 6 hours


def compute_tag_scores(days=90):
    """
    Return {tag_id: {"score": int, "opens": int, "engaged": int,
    "full_text": int, "shares": int, "review_count": int}} for all curated
    tags that have at least one active review with engagement data.

    Results are cached for 6 hours.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    cutoff = timezone.now() - timezone.timedelta(days=days)
    review_ct = ContentType.objects.get_for_model(Review)

    # Aggregate per-review engagement from human-confidence events
    review_rows = list(
        AnalyticsEvent.objects.filter(
            content_type=review_ct,
            timestamp__gte=cutoff,
            automated=False,
        )
        .values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
            total_shares=Count("id", filter=Q(event_type__in=SHARE_EVENT_TYPES)),
        )
    )

    if not review_rows:
        cache.set(CACHE_KEY, {}, CACHE_TIMEOUT)
        return {}

    # Fetch reviews with their curated tags in one query
    review_ids = [r["object_id"] for r in review_rows if r["object_id"]]
    reviews = (
        Review.objects.filter(id__in=review_ids, active=True)
        .select_related("article")
        .prefetch_related("article__tags")
    )
    # Build {review_id: [curated_tag_ids]}
    review_tag_map = {}
    for review in reviews:
        review_tag_map[review.id] = [t.id for t in review.article.tags.all() if t.curated and t.active]

    # Aggregate scores per tag
    tag_totals = defaultdict(
        lambda: {"score": 0, "opens": 0, "engaged": 0, "full_text": 0, "shares": 0, "review_count": 0}
    )
    seen_reviews_per_tag = defaultdict(set)

    for row in review_rows:
        rid = row["object_id"]
        tag_ids = review_tag_map.get(rid, [])
        if not tag_ids:
            continue
        score = row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
        for tid in tag_ids:
            tag_totals[tid]["opens"] += row["opens"]
            tag_totals[tid]["engaged"] += row["engaged_views"]
            tag_totals[tid]["full_text"] += row["full_text_clicks"]
            tag_totals[tid]["shares"] += row["total_shares"]
            tag_totals[tid]["score"] += score
            seen_reviews_per_tag[tid].add(rid)

    for tid, review_ids_set in seen_reviews_per_tag.items():
        tag_totals[tid]["review_count"] = len(review_ids_set)

    result = dict(tag_totals)
    cache.set(CACHE_KEY, result, CACHE_TIMEOUT)
    return result
