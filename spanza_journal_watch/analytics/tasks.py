import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from config.celery_app import app as celery_app
from spanza_journal_watch.analytics.models import AnalyticsEvent, HumanConfidence

logger = logging.getLogger(__name__)


@celery_app.task
def downgrade_singleton_visitors_task(min_age_hours=2.0, dry_run=False):
    """
    Reclassify visitors with a single non-JS-verified event as
    ``suspected_automated``.

    A visitor with exactly one event and no JS verification is the classic
    crawler signature: fetched a page, never ran scripts, never came back.
    The UA-marker list catches the ones that self-identify; this catches
    the ones that lie about their UA.

    Only touches events currently classified as ``probable_human`` (so it's
    idempotent), and preserves newsletter-referred visits.
    """
    cutoff = timezone.now() - timedelta(hours=min_age_hours)

    singleton_visitor_ids = (
        AnalyticsEvent.objects.filter(visitor_id__isnull=False)
        .values("visitor_id")
        .annotate(event_count=Count("id"))
        .filter(event_count=1)
        .values_list("visitor_id", flat=True)
    )

    candidates = AnalyticsEvent.objects.filter(
        visitor_id__in=singleton_visitor_ids,
        js_verified=False,
        human_confidence=HumanConfidence.PROBABLE_HUMAN,
        timestamp__lt=cutoff,
    ).exclude(referrer_category="newsletter")

    if dry_run:
        count = candidates.count()
        logger.info("downgrade_singleton_visitors dry run: would downgrade %d event(s)", count)
        return {"would_downgrade": count, "downgraded": 0, "dry_run": True}

    downgraded = candidates.update(
        automated=True,
        human_confidence=HumanConfidence.SUSPECTED_AUTOMATED,
    )
    logger.info("downgrade_singleton_visitors: downgraded %d event(s) to suspected_automated", downgraded)
    return {"downgraded": downgraded, "dry_run": False}
