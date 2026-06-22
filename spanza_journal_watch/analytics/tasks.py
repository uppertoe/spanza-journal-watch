import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from config.celery_app import app as celery_app
from spanza_journal_watch.analytics.models import AnalyticsEvent, AutomatedRequestCount, HumanConfidence

logger = logging.getLogger(__name__)


def _apply_downgrade(candidates, *, label, dry_run):
    if dry_run:
        count = candidates.count()
        logger.info("%s dry run: would downgrade %d event(s)", label, count)
        return {"would_downgrade": count, "downgraded": 0, "dry_run": True}

    # Aggregate BEFORE the update so the queryset still resolves; feed the
    # results into AutomatedRequestCount so the overview's "filtered as bot"
    # card reflects both record-time rejections AND post-hoc downgrades.
    bucket_counts = list(
        candidates.annotate(day=TruncDate("timestamp")).values("day", "event_type").annotate(n=Count("id"))
    )

    downgraded = candidates.update(
        automated=True,
        human_confidence=HumanConfidence.SUSPECTED_AUTOMATED,
    )

    for bucket in bucket_counts:
        AutomatedRequestCount.bump(bucket["event_type"], reason=label[:32], date=bucket["day"], by=bucket["n"])

    logger.info("%s: downgraded %d event(s) to suspected_automated", label, downgraded)
    return {"downgraded": downgraded, "dry_run": False}


@celery_app.task
def downgrade_singleton_visitors_task(min_age_hours=0.5, dry_run=False):
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

    return _apply_downgrade(candidates, label="downgrade_singleton_visitors", dry_run=dry_run)


@celery_app.task
def downgrade_no_js_burst_visitors_task(min_events=5, min_age_hours=0.5, dry_run=False):
    """
    Reclassify visitors with many events but zero JS-verified ones as
    ``suspected_automated``.

    Catches cookie-persisting scrapers that evade the singleton sweeper by
    hammering many URLs under the same ``visitor_id`` without running JS.
    Default threshold of 5 events keeps real JS-disabled readers out of
    scope, since they'd still typically trigger at least one interactive
    beacon eventually.

    Only touches events currently classified as ``probable_human`` and
    preserves newsletter-referred visits, matching the singleton sweeper.
    """
    cutoff = timezone.now() - timedelta(hours=min_age_hours)

    burst_visitor_ids = (
        AnalyticsEvent.objects.filter(visitor_id__isnull=False, timestamp__lt=cutoff)
        .values("visitor_id")
        .annotate(event_count=Count("id"), js_count=Count("id", filter=Q(js_verified=True)))
        .filter(event_count__gte=min_events, js_count=0)
        .values_list("visitor_id", flat=True)
    )

    candidates = AnalyticsEvent.objects.filter(
        visitor_id__in=burst_visitor_ids,
        js_verified=False,
        human_confidence=HumanConfidence.PROBABLE_HUMAN,
        timestamp__lt=cutoff,
    ).exclude(referrer_category="newsletter")

    return _apply_downgrade(candidates, label="downgrade_no_js_burst_visitors", dry_run=dry_run)


@celery_app.task
def prune_automated_events_task(retention_days=90, batch_size=5000, dry_run=False):
    """
    Delete bot-classified analytics events older than ``retention_days``.

    Crawler hits dominate raw event volume (~95% of recorded page visits), and
    the downgrade sweepers flag them as ``automated=True``. Those rows are pure
    noise once aggregated — the overview's "filtered as bot" totals live in
    ``AutomatedRequestCount`` and are untouched here — so they can be pruned to
    keep the ``analytics_analyticsevent`` table from growing without bound.

    Only ``automated=True`` events are removed. Genuine human events (and
    newsletter-referred visits, which the sweepers never downgrade) are kept
    indefinitely, so human-engagement reporting is unaffected.

    Deletes in batches to keep each transaction small and avoid holding a long
    lock on a table that's written on every request. The ``(automated,
    timestamp)`` index covers the filter.
    """
    cutoff = timezone.now() - timedelta(days=retention_days)
    candidates = AnalyticsEvent.objects.filter(automated=True, timestamp__lt=cutoff)

    if dry_run:
        count = candidates.count()
        logger.info(
            "prune_automated_events dry run: would delete %d event(s) older than %s",
            count,
            cutoff.date(),
        )
        return {"would_delete": count, "deleted": 0, "dry_run": True}

    total_deleted = 0
    while True:
        batch_ids = list(candidates.values_list("id", flat=True)[:batch_size])
        if not batch_ids:
            break
        AnalyticsEvent.objects.filter(id__in=batch_ids).delete()
        total_deleted += len(batch_ids)
        if len(batch_ids) < batch_size:
            break

    logger.info(
        "prune_automated_events: deleted %d event(s) older than %s",
        total_deleted,
        cutoff.date(),
    )
    return {"deleted": total_deleted, "dry_run": False}
