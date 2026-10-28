import logging
from datetime import timedelta

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import MultipleObjectsReturned
from django.db import IntegrityError, models
from django.db.models import F
from django.db.models.functions import TruncDate
from django.template.loader import render_to_string
from django.utils import timezone

from spanza_journal_watch.analytics.utils import (
    REFERRER_DIRECT,
    categorize_referrer,
    classify_automated_reason,
    classify_event_confidence,
    client_hints_anomaly,
    client_hints_enforced,
    extract_referrer_domain,
    scrub_private_metadata,
    scrub_private_url,
    session_fingerprint,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber

logger = logging.getLogger(__name__)

# Mail-gateway link scanners follow every link in a newsletter within seconds;
# a reader follows one to three. A subscriber's clicks that reach this many
# distinct destinations within the window either side of a click are a scan.
SCANNER_BURST_MIN_URLS = 6
SCANNER_BURST_WINDOW = timedelta(seconds=15)
SCANNER_BURST_REASON = "scanner_burst"

NEWSLETTER_CLICK_SOURCE = "newsletter_click"
# How far a newsletter_click event's timestamp may sit from its click's, for
# events recorded before the click ID was stored in their metadata.
NEWSLETTER_CLICK_EVENT_SLACK = timedelta(seconds=2)

REFERRER_CHOICES = [
    ("newsletter", "Newsletter"),
    ("search", "Search engine"),
    ("social", "Social media"),
    ("direct", "Direct"),
    ("internal", "Internal"),
    ("other", "Other"),
]


class HumanConfidence(models.TextChoices):
    SUSPECTED_AUTOMATED = "suspected_automated", "Suspected automated"
    PROBABLE_HUMAN = "probable_human", "Probable human"
    KNOWN_SUBSCRIBER_HUMAN = "known_subscriber_human", "Known subscriber human"


def _get_subscriber_for_analytics(subscriber_id, *, log_context):
    if not subscriber_id:
        return None

    try:
        return Subscriber.objects.get(id=subscriber_id)
    except (Subscriber.DoesNotExist, MultipleObjectsReturned) as error:
        logger.warning("Unable to attach subscriber to %s: %s %s", log_context, error, subscriber_id)
        return None


class NewsletterOpen(models.Model):
    newsletter = models.ForeignKey(Newsletter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE)
    user_agent = models.TextField(blank=True, default="")
    automated = models.BooleanField(default=False)
    human_confidence = models.CharField(
        max_length=32,
        choices=HumanConfidence,
        default=HumanConfidence.PROBABLE_HUMAN,
    )
    # The deciding signal when automated is True ("apple_mpp", "send_window",
    # "ua_marker", ...); blank for human opens.
    automated_reason = models.CharField(max_length=32, blank=True, default="")

    @staticmethod
    def render_tracking_pixel(tracker):
        """Return the pixel ``<img>`` for the subscriber and newsletter ``tracker`` is built for."""
        return render_to_string("analytics/email_pixel.html", {"pixel_url": tracker.pixel_url()})

    @classmethod
    def get_between_timestamps(cls, newsletter, start_timestamp, end_timestamp):
        return cls.objects.filter(newsletter=newsletter, timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    def __str__(self):
        return str(self.subscriber)


class NewsletterClick(models.Model):
    newsletter = models.ForeignKey(Newsletter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE)
    user_agent = models.TextField(blank=True, default="")
    automated = models.BooleanField(default=False)
    human_confidence = models.CharField(
        max_length=32,
        choices=HumanConfidence,
        default=HumanConfidence.PROBABLE_HUMAN,
    )
    destination_url = models.URLField(max_length=512, blank=True, default="")
    # The deciding signal when automated is True ("scanner_burst",
    # "send_window", "ua_marker", ...); blank for human clicks.
    automated_reason = models.CharField(max_length=32, blank=True, default="")

    @classmethod
    def get_between_timestamps(cls, newsletter, start_timestamp, end_timestamp):
        return cls.objects.filter(newsletter=newsletter, timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    @classmethod
    def mark_scanner_burst(cls, click_ids):
        """Mark ``click_ids`` and their newsletter_click events as a scanner burst.

        Clicks already classified as automated keep their reason. Events are
        matched by the click ID stored in their metadata; events recorded
        before that link existed are matched by subscriber, destination and
        time. Returns the number of clicks marked.
        """
        click_ids = list(click_ids)
        clicks = cls.objects.filter(pk__in=click_ids)
        event_filter = models.Q(metadata__newsletter_click_id__in=click_ids)
        for subscriber_id, timestamp, url in clicks.values_list("subscriber_id", "timestamp", "destination_url"):
            event_filter |= models.Q(
                subscriber_id=subscriber_id,
                metadata__destination_url=url,
                metadata__newsletter_click_id__isnull=True,
                timestamp__gte=timestamp - NEWSLETTER_CLICK_EVENT_SLACK,
                timestamp__lte=timestamp + NEWSLETTER_CLICK_EVENT_SLACK,
            )

        marked = clicks.filter(automated=False).update(
            automated=True,
            automated_reason=SCANNER_BURST_REASON,
            human_confidence=HumanConfidence.SUSPECTED_AUTOMATED,
        )

        events = AnalyticsEvent.objects.filter(
            event_filter,
            source=NEWSLETTER_CLICK_SOURCE,
            automated=False,
        )
        # Count before the update, as the downgrade sweepers do, so the
        # "filtered as bot" totals include events marked after the fact.
        buckets = list(
            events.annotate(day=TruncDate("timestamp")).values("day", "event_type").annotate(n=models.Count("id"))
        )
        events.update(automated=True, human_confidence=HumanConfidence.SUSPECTED_AUTOMATED)
        for bucket in buckets:
            AutomatedRequestCount.bump(
                bucket["event_type"], reason=SCANNER_BURST_REASON, date=bucket["day"], by=bucket["n"]
            )
        return marked

    @classmethod
    def burst_click_ids(cls, clicks):
        """Return the IDs among ``clicks`` that fall in a scanner burst.

        ``clicks`` is an iterable of ``(id, timestamp, destination_url)`` for
        one subscriber and newsletter, ordered by timestamp. A click is in a
        burst when the clicks within SCANNER_BURST_WINDOW either side of it
        reach SCANNER_BURST_MIN_URLS distinct destinations; every click in that
        window is then part of the burst.
        """
        clicks = list(clicks)
        burst = set()
        start = end = 0
        for _pk, timestamp, _url in clicks:
            while clicks[start][1] < timestamp - SCANNER_BURST_WINDOW:
                start += 1
            while end < len(clicks) and clicks[end][1] <= timestamp + SCANNER_BURST_WINDOW:
                end += 1
            window = clicks[start:end]
            if len({url for _id, _ts, url in window}) >= SCANNER_BURST_MIN_URLS:
                burst.update(pk for pk, _ts, _url in window)
        return burst

    def check_scanner_burst(self):
        """Mark this click's burst, if it is part of one. Returns True if so."""
        nearby = (
            NewsletterClick.objects.filter(
                subscriber_id=self.subscriber_id,
                newsletter_id=self.newsletter_id,
                timestamp__gte=self.timestamp - 2 * SCANNER_BURST_WINDOW,
                timestamp__lte=self.timestamp + 2 * SCANNER_BURST_WINDOW,
            )
            .exclude(destination_url="")
            .order_by("timestamp", "pk")
            .values_list("pk", "timestamp", "destination_url")
        )
        burst = self.burst_click_ids(nearby)
        if self.pk not in burst:
            return False
        NewsletterClick.mark_scanner_burst(burst)
        self.refresh_from_db(fields=["automated", "automated_reason", "human_confidence"])
        return True

    def __str__(self):
        return str(self.subscriber)


class AnalyticsEvent(models.Model):
    HumanConfidence = HumanConfidence  # backward-compatible alias

    class EventType(models.TextChoices):
        REVIEW_OPEN = "review_open", "Review open"
        REVIEW_ENGAGED = "review_engaged", "Review engaged"
        REVIEW_FULL_TEXT_CLICK = "review_full_text_click", "Review full text click"
        REVIEW_RELATED_CLICK = "review_related_click", "Review related-link click"
        REVIEW_SHARE_COPY_LINK = "review_share_copy_link", "Review shared via copy link"
        REVIEW_SHARE_EMAIL = "review_share_email", "Review shared via email"
        REVIEW_SHARE_NATIVE = "review_share_native", "Review shared via native share"
        REVIEW_SHARE_BLUESKY = "review_share_bluesky", "Review shared via Bluesky"
        REVIEW_SHARE_X = "review_share_x", "Review shared via X"
        REVIEW_SHARE_FACEBOOK = "review_share_facebook", "Review shared via Facebook"
        SEARCH = "search", "Search performed"
        SEARCH_RESULT_CLICK = "search_result_click", "Search result clicked"
        PAGE_VISIT = "page_visit", "Page visit"
        JOURNAL_BROWSER_VISIT = "journal_browser_visit", "Journal browser visit"
        JOURNAL_ARTICLE_INTERACT = "journal_article_interact", "Journal article interaction"
        JOURNAL_FULL_TEXT_CLICK = "journal_full_text_click", "Journal full text click"
        JOURNAL_STAR = "journal_star", "Journal article starred"
        JOURNAL_RECOMMEND = "journal_recommend", "Journal article recommended"
        JOURNAL_MARK_READ = "journal_mark_read", "Journal article marked read"
        JOURNAL_ARCHIVE = "journal_archive", "Journal article archived"
        JOURNAL_SEARCH = "journal_search", "Journal browser search"
        JOURNAL_SELECT = "journal_select", "Journal selected"
        NEWSLETTER_SUBSCRIBE = "newsletter_subscribe", "Newsletter subscribe"
        CPD_TRACKING_TOGGLE = "cpd_tracking_toggle", "CPD tracking toggled"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, blank=True, null=True)
    object_id = models.PositiveIntegerField(blank=True, null=True)
    content_object = GenericForeignKey("content_type", "object_id")
    event_type = models.CharField(max_length=48, choices=EventType.choices)
    timestamp = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=64, blank=True, default="")
    duration_ms = models.PositiveIntegerField(blank=True, null=True)
    scroll_depth = models.PositiveSmallIntegerField(blank=True, null=True)
    metadata = models.JSONField(blank=True, default=dict)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, blank=True, null=True)
    user_agent = models.TextField(blank=True, default="")
    automated = models.BooleanField(default=False)
    session_key = models.CharField(max_length=64, blank=True, default="")
    human_confidence = models.CharField(
        max_length=32,
        choices=HumanConfidence,
        default=HumanConfidence.PROBABLE_HUMAN,
    )
    visitor_id = models.UUIDField(null=True, blank=True, db_index=True)
    referrer_category = models.CharField(max_length=16, blank=True, default="", choices=REFERRER_CHOICES)
    referrer_domain = models.CharField(max_length=255, blank=True, default="")
    landing_page = models.CharField(max_length=512, blank=True, default="")
    session_sequence = models.PositiveIntegerField(default=0)
    js_verified = models.BooleanField(default=False)
    share_token = models.CharField(max_length=32, blank=True, default="", db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "timestamp"]),
            models.Index(fields=["content_type", "object_id", "timestamp"]),
            models.Index(fields=["source", "timestamp"]),
            models.Index(fields=["session_key"]),
            models.Index(fields=["automated", "timestamp"], name="analytics_a_auto_ts_idx"),
        ]
        ordering = ("-timestamp",)

    @classmethod
    def record_event(
        cls,
        *,
        event_type,
        request=None,
        content_object=None,
        subscriber_id=None,
        source="",
        duration_ms=None,
        scroll_depth=None,
        metadata=None,
        js_verified=False,
        referrer=None,
        utm_source=None,
        landing_page=None,
        share_token=None,
        automated=None,
        automated_reason="",
    ):
        """
        The referrer/utm_source/landing_page/share_token kwargs carry page
        context from the JS beacon payload; beacon requests are same-origin so
        their own headers/query string can't supply these. When omitted (server-
        rendered call sites like newsletter clicks), request headers are used.

        ``automated`` passes in a classification the caller has already made
        (a newsletter click carries its NewsletterClick's verdict). The request
        is then not reclassified, and an automated event is kept, flagged, so a
        later burst check can find it; it is counted in AutomatedRequestCount
        under ``automated_reason`` as a dropped request would be.
        """
        subscriber = _get_subscriber_for_analytics(subscriber_id, log_context="analytics event")

        user_agent = ""
        session_key = ""
        visitor_id = None
        referrer_category = REFERRER_DIRECT
        referrer_domain = ""
        session_sequence = 0
        if request is not None:
            if automated is None:
                reason = classify_automated_reason(request, event_type=event_type)
                if reason is not None:
                    AutomatedRequestCount.bump(event_type, reason=reason)
                    return None
            if not client_hints_enforced():
                # Observation mode: count and tag client-hint anomalies without
                # dropping the row, so the rule can be judged against real
                # traffic before ANALYTICS_ENFORCE_CLIENT_HINTS is switched on.
                anomaly = client_hints_anomaly(request)
                if anomaly is not None:
                    AutomatedRequestCount.bump(event_type, reason=f"observe_{anomaly}")
                    metadata = {**(metadata or {}), "client_hints": anomaly.removeprefix("client_hints_")}
            user_agent = request.headers.get("user-agent", "")
            session_key = session_fingerprint(request.session.session_key)
            visitor_id = getattr(request, "analytics_visitor_id", None) or None
            referrer_category = categorize_referrer(request, referrer=referrer, utm_source=utm_source)
            referrer_domain = extract_referrer_domain(request, referrer=referrer)
        # Confirmation, invite and account URLs are kept to their prefix.
        landing_page = scrub_private_url(landing_page or "")[:512]
        metadata = scrub_private_metadata(metadata or {})
        share_token = (share_token or "")[:32]
        automated = bool(automated)
        if automated:
            AutomatedRequestCount.bump(event_type, reason=(automated_reason or "")[:32])
        human_confidence = classify_event_confidence(automated=automated, subscriber=subscriber)

        content_type = None
        object_id = None
        if content_object is not None:
            content_type = ContentType.objects.get_for_model(content_object)
            object_id = content_object.pk

        scroll_depth = max(0, min(int(scroll_depth), 100)) if scroll_depth is not None else None
        duration_ms = max(0, int(duration_ms)) if duration_ms is not None else None

        return cls.objects.create(
            content_type=content_type,
            object_id=object_id,
            event_type=event_type,
            source=(source or "")[:64],
            duration_ms=duration_ms,
            scroll_depth=scroll_depth,
            metadata=metadata or {},
            subscriber=subscriber,
            user_agent=user_agent,
            automated=automated,
            session_key=session_key,
            human_confidence=human_confidence,
            visitor_id=visitor_id,
            referrer_category=referrer_category,
            referrer_domain=referrer_domain,
            landing_page=landing_page,
            session_sequence=session_sequence,
            js_verified=js_verified,
            share_token=share_token,
        )

    def __str__(self):
        label = self.get_event_type_display()
        if self.content_object:
            return f"{label}: {self.content_object}"
        return label


# Hard-to-fake actions that mark a visitor as a real human: a click on a
# specific element (full text, share, search result, journal select, CPD toggle).
# This is the single definition shared by the "engaged human" KPI, the share-
# attribution metric, and the UA-cohort bot sweeper's protected set.
#
# Excluded on purpose, because a 2026-06 prod audit showed JS-executing bots game
# them: scroll depth, bare `search` submissions, and REVIEW_ENGAGED (a 5s
# scroll-dwell — its "only" population was 100% non-returning bots). A click on a
# specific element is the cheapest signal these fleets don't produce.
DELIBERATE_INTERACTION_EVENT_TYPES = frozenset(
    [
        AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
        AnalyticsEvent.EventType.SEARCH_RESULT_CLICK,
        AnalyticsEvent.EventType.CPD_TRACKING_TOGGLE,
        AnalyticsEvent.EventType.JOURNAL_SELECT,
    ]
)


class AutomatedRequestCount(models.Model):
    """Daily aggregate of requests dropped by record_event's bot short-circuit.

    Replaces per-row AnalyticsEvent rows for automated traffic (which we no
    longer persist) so the overview dashboard can still surface a bot-volume
    signal without the write amplification.
    """

    date = models.DateField()
    event_type = models.CharField(max_length=48, choices=AnalyticsEvent.EventType.choices)
    # Coarse deciding signal (e.g. "ua_marker", "empty_ua", "downgrade:singleton").
    # Low cardinality on purpose; "" for rows written before reason capture.
    reason = models.CharField(max_length=32, default="", blank=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("date", "event_type", "reason")
        indexes = [models.Index(fields=["date"])]
        ordering = ("-date", "event_type")

    @classmethod
    def bump(cls, event_type, *, reason="", date=None, by=1):
        target_date = date or timezone.localdate()
        lookup = {"date": target_date, "event_type": event_type, "reason": reason}
        updated = cls.objects.filter(**lookup).update(count=F("count") + by)
        if updated:
            return
        try:
            cls.objects.create(**lookup, count=by)
        except IntegrityError:
            cls.objects.filter(**lookup).update(count=F("count") + by)

    def __str__(self):
        return f"{self.date} {self.event_type}: {self.count}"
