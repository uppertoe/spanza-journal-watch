import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from config.celery_app import app as celery_app
from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
    AutomatedRequestCount,
    HumanConfidence,
)
from spanza_journal_watch.analytics.utils import stale_browser_reason

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
def downgrade_js_singleton_visitors_task(min_age_hours=24, dry_run=False):
    """
    Reclassify direct, JS-verified, one-event visitors that never came back.

    The 2026-09 audit found the crawler fleet had moved to a headless browser:
    each visitor lands once, direct, fires the page-load beacon (so it is
    JS-verified and the no-JS singleton sweeper never sees it) and is never
    seen again. 285 of 335 unswept visitors in one week fitted that shape.

    A person who arrives the same way almost always leaves more than one event
    (a scroll beacon, an impression, a second page). The visitor is given a day
    to come back before being swept. Search, newsletter and other referred
    arrivals are exempt, as are subscribers; only ``probable_human`` rows are
    touched, so the task is idempotent.
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
        js_verified=True,
        subscriber__isnull=True,
        human_confidence=HumanConfidence.PROBABLE_HUMAN,
        referrer_category__in=["direct", ""],
        timestamp__lt=cutoff,
    )

    return _apply_downgrade(candidates, label="downgrade_js_singleton_visitors", dry_run=dry_run)


@celery_app.task
def downgrade_stale_browser_visitors_task(lookback_days=30, min_age_hours=0.5, dry_run=False):
    """
    Reclassify visitors whose user-agent claims a browser too old to be in use.

    The rotating fleet seen in 2026-09 spreads itself across Chrome majors 104
    to 117, three to four years old at the time, and a 2019 iPhone OS 13
    Safari. ``stale_browser_reason`` decides what counts as stale from the date,
    so the threshold never needs a code change.

    Visitors with a deliberate interaction, a subscriber match or a newsletter
    referral are protected, in case a locked-down hospital machine really is
    that old. Only ``probable_human`` rows are touched, so the task is
    idempotent.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=min_age_hours)

    base = (
        AnalyticsEvent.objects.filter(
            timestamp__gte=now - timedelta(days=lookback_days),
            timestamp__lt=cutoff,
            human_confidence=HumanConfidence.PROBABLE_HUMAN,
            visitor_id__isnull=False,
        )
        .exclude(referrer_category="newsletter")
        .exclude(user_agent="")
    )
    protected = set(
        base.filter(Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES) | Q(subscriber__isnull=False)).values_list(
            "visitor_id", flat=True
        )
    )
    stale_uas = [
        ua
        for ua in base.order_by().values_list("user_agent", flat=True).distinct()
        if stale_browser_reason(ua) is not None
    ]
    if not stale_uas:
        logger.info("downgrade_stale_browser: no stale user-agents found")
        return {"downgraded": 0, "user_agents": 0, "dry_run": dry_run}

    candidates = base.filter(user_agent__in=stale_uas).exclude(visitor_id__in=protected)
    result = _apply_downgrade(candidates, label="downgrade_stale_browser", dry_run=dry_run)
    result["user_agents"] = len(stale_uas)
    return result


@celery_app.task
def downgrade_ua_cohort_visitors_task(
    min_cohort_size=25,
    lookback_days=30,
    min_age_hours=0.5,
    dry_run=False,
    shape_cohort_size=10,
    shape_single_event_ratio=0.8,
):
    """
    Reclassify JS-executing bot fleets that share one user-agent as
    ``suspected_automated``.

    The singleton and no-JS-burst sweepers both miss the modern fleet seen in the
    2026-06 audit: ~300+ "visitors" on a single identical Windows/Chrome UA that
    run JS and scroll many pages, but never take a deliberate action and never
    return. They evade the other sweepers precisely because they are JS-verified
    and multi-event.

    Signature exploited (all from existing data, no new signal):
      - a single user-agent shared by >= ``min_cohort_size`` distinct visitors
        within the lookback window,
      - where those visitors fired ZERO deliberate interactions — a click on a
        specific element (``DELIBERATE_INTERACTION_EVENT_TYPES``); a 5s
        scroll-dwell (REVIEW_ENGAGED) does NOT protect, as auto-scrollers trip it.

    False positives are bounded two ways: the threshold is conservative (a real
    UA shared by that many people on a niche site is implausible), and any
    visitor who took a deliberate action — or matched a subscriber, or came via
    the newsletter — is protected and never swept, even if they share the UA.
    Only touches ``probable_human`` events, so it is idempotent.

    Smaller cohorts, from ``shape_cohort_size`` visitors up, are swept on shape
    rather than size alone. By 2026-09 the fleet had learned to rotate the
    Chrome major per visitor, keeping every exact UA just under the size
    threshold. A cohort is swept on shape when nobody in it returned on a
    second day, nobody arrived from a search engine, and at least
    ``shape_single_event_ratio`` of its visitors left a single event. A real
    user-agent shared by ten people always breaks at least one of those.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=min_age_hours)
    window_start = now - timedelta(days=lookback_days)

    base = (
        AnalyticsEvent.objects.filter(
            timestamp__gte=window_start,
            timestamp__lt=cutoff,
            human_confidence=HumanConfidence.PROBABLE_HUMAN,
            visitor_id__isnull=False,
        )
        .exclude(referrer_category="newsletter")
        .exclude(user_agent="")
    )

    # Visitors that are real humans (a hard click or matched subscriber) are
    # protected regardless of which UA they share. The deliberate set excludes
    # REVIEW_ENGAGED (gameable 5s dwell), so dwell-only bots are not shielded.
    protected = set(
        base.filter(Q(event_type__in=DELIBERATE_INTERACTION_EVENT_TYPES) | Q(subscriber__isnull=False)).values_list(
            "visitor_id", flat=True
        )
    )

    unprotected = base.exclude(visitor_id__in=protected)
    ua_sizes = unprotected.values("user_agent").annotate(n=Count("visitor_id", distinct=True))

    # User-agents whose *non-protected* visitor count crosses the cohort size.
    cohort_uas = set(ua_sizes.filter(n__gte=min_cohort_size).values_list("user_agent", flat=True))

    # Smaller cohorts are judged on shape.
    shape_uas = list(
        ua_sizes.filter(n__gte=shape_cohort_size, n__lt=min_cohort_size).values_list("user_agent", flat=True)
    )
    shape_cohorts = 0
    if shape_uas:
        per_visitor = (
            unprotected.filter(user_agent__in=shape_uas)
            .values("user_agent", "visitor_id")
            .annotate(
                events=Count("id"),
                days=Count(TruncDate("timestamp"), distinct=True),
                searches=Count("id", filter=Q(referrer_category="search")),
            )
        )
        by_ua = {}
        for row in per_visitor:
            by_ua.setdefault(row["user_agent"], []).append(row)
        for ua, visitors in by_ua.items():
            if any(v["days"] > 1 or v["searches"] for v in visitors):
                continue
            single = sum(1 for v in visitors if v["events"] == 1)
            if single / len(visitors) >= shape_single_event_ratio:
                cohort_uas.add(ua)
                shape_cohorts += 1

    if not cohort_uas:
        logger.info("downgrade_ua_cohort: no bot cohorts >= %d found", min_cohort_size)
        return {"downgraded": 0, "cohorts": 0, "shape_cohorts": 0, "dry_run": dry_run}

    candidates = unprotected.filter(user_agent__in=cohort_uas)
    result = _apply_downgrade(candidates, label="downgrade_ua_cohort", dry_run=dry_run)
    result["cohorts"] = len(cohort_uas)
    result["shape_cohorts"] = shape_cohorts
    return result


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
