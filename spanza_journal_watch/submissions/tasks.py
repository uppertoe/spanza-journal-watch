import logging

import requests

from config.celery_app import app as celery_app
from spanza_journal_watch.utils import indexnow

logger = logging.getLogger(__name__)


@celery_app.task(autoretry_for=(requests.RequestException,), retry_backoff=60, max_retries=3)
def submit_urls_to_indexnow(paths):
    return indexnow.submit_urls(paths)


@celery_app.task
def draft_review_headline_task(review_id):
    """Draft a headline and bottom line for one review into its draft fields."""
    from django.utils import timezone

    from spanza_journal_watch.submissions.headlines import draft_review_headline
    from spanza_journal_watch.submissions.models import Review

    review = Review.objects.select_related("article", "author").get(pk=review_id)
    draft = draft_review_headline(review)
    if not draft:
        return False
    review.draft_headline = draft["headline"]
    review.draft_bottom_line = draft["bottom_line"]
    review.draft_generated_at = timezone.now()
    review.save(update_fields=["draft_headline", "draft_bottom_line", "draft_generated_at", "modified"])
    return True


@celery_app.task
def draft_missing_headlines_task(limit=0):
    """Draft for every live review that has neither a headline nor a waiting draft."""
    from spanza_journal_watch.submissions.models import Review

    qs = Review.objects.filter(active=True, editorial_headline="", draft_headline="").order_by("-created")
    if limit:
        qs = qs[:limit]
    drafted = 0
    for review_id in qs.values_list("pk", flat=True):
        if draft_review_headline_task(review_id):
            drafted += 1
    return drafted


def queue_indexnow_submission(paths):
    """Queue an IndexNow ping without letting a broker outage break the caller."""
    if not indexnow.indexnow_enabled() or not paths:
        return
    try:
        submit_urls_to_indexnow.delay(list(paths))
    except Exception:  # noqa: BLE001 - never let search-engine pings break publishing
        logger.exception("Could not queue IndexNow submission for %d path(s)", len(paths))
