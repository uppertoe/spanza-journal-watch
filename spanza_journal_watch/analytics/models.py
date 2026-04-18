import logging

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import MultipleObjectsReturned
from django.db import models
from django.template.loader import render_to_string

from spanza_journal_watch.analytics.utils import (
    REFERRER_DIRECT,
    categorize_referrer,
    classify_event_confidence,
    extract_referrer_domain,
    is_probable_automated_event,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.utils.functions import get_domain_url

logger = logging.getLogger(__name__)

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
        choices=HumanConfidence.choices,
        default=HumanConfidence.PROBABLE_HUMAN,
    )

    @staticmethod
    def render_tracking_pixel(email, token):
        context = {"email": email, "token": token, "domain": get_domain_url()}
        template = "analytics/email_pixel.html"
        return render_to_string(template, context)

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
        choices=HumanConfidence.choices,
        default=HumanConfidence.PROBABLE_HUMAN,
    )
    destination_url = models.URLField(max_length=512, blank=True, default="")

    @staticmethod
    def generate_tracking_link(email, token):
        """Redirects to the url immediately following this tag"""
        context = {"email": email, "token": token, "domain": get_domain_url()}
        template = "analytics/email_newsletter_link.txt"
        return render_to_string(template, context)

    @classmethod
    def get_between_timestamps(cls, newsletter, start_timestamp, end_timestamp):
        return cls.objects.filter(newsletter=newsletter, timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    def __str__(self):
        return str(self.subscriber)


class AnalyticsEvent(models.Model):
    HumanConfidence = HumanConfidence  # backward-compatible alias

    class EventType(models.TextChoices):
        REVIEW_OPEN = "review_open", "Review open"
        REVIEW_ENGAGED = "review_engaged", "Review engaged"
        REVIEW_FULL_TEXT_CLICK = "review_full_text_click", "Review full text click"
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
        JOURNAL_SELECT = "journal_select", "Journal selected"

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
        choices=HumanConfidence.choices,
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
    ):
        subscriber = _get_subscriber_for_analytics(subscriber_id, log_context="analytics event")

        user_agent = ""
        automated = False
        session_key = ""
        visitor_id = None
        referrer_category = REFERRER_DIRECT
        referrer_domain = ""
        landing_page = ""
        session_sequence = 0
        share_token = ""
        if request is not None:
            user_agent = request.headers.get("user-agent", "")
            automated = is_probable_automated_event(request, event_type=event_type)
            session_key = request.session.session_key or ""
            visitor_id = getattr(request, "analytics_visitor_id", None) or None
            referrer_category = categorize_referrer(request)
            referrer_domain = extract_referrer_domain(request)
            landing_page = request.session.get("analytics_landing_page", "")
            share_token = request.session.get("analytics_share_token", "")
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
