import logging

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import MultipleObjectsReturned
from django.db import models
from django.template.loader import render_to_string

from spanza_journal_watch.analytics.utils import is_probable_automated_event
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.utils.functions import get_domain_url

logger = logging.getLogger(__name__)


class NewsletterOpen(models.Model):
    newsletter = models.ForeignKey(Newsletter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE)
    user_agent = models.TextField(blank=True, default="")
    automated = models.BooleanField(default=False)

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


class PageView(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    timestamp = models.DateTimeField(auto_now_add=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, blank=True, null=True)
    user_agent = models.TextField(blank=True, default="")
    automated = models.BooleanField(default=False)
    session_key = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]
        ordering = ("-timestamp",)

    def get_instance(self):
        return self.content_type.get_object_for_this_type(id=self.object_id)

    @staticmethod
    def filter_between_timestamps(queryset, start_timestamp, end_timestamp):
        return queryset.filter(timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    @classmethod
    def get_page_views(cls, content_type, object_id):
        return cls.objects.filter(content_type=content_type, object_id=object_id)

    @classmethod
    def record_view(cls, object, subscriber_id=None, request=None):
        content_type = ContentType.objects.get_for_model(object)
        id = object.id

        # Attempt to find a subscriber in the session
        try:
            subscriber = Subscriber.objects.get(id=subscriber_id)
        except (Subscriber.DoesNotExist, MultipleObjectsReturned) as e:
            subscriber = None
            logger.warning("Unable to attach subscriber to pageview: %s %s", e, subscriber_id)

        user_agent = ""
        automated = False
        session_key = ""
        if request is not None:
            if not request.session.session_key:
                request.session.create()
            user_agent = request.headers.get("user-agent", "")
            automated = is_probable_automated_event(request)
            session_key = request.session.session_key or ""

        view = cls(
            content_type=content_type,
            object_id=id,
            subscriber=subscriber,
            user_agent=user_agent,
            automated=automated,
            session_key=session_key,
        )
        view.save()

    def __str__(self):
        datetime = self.timestamp.strftime("%d/%m/%Y, %H:%M:%S")
        return f"{str(self.content_object)}: {datetime}"


class AnalyticsEvent(models.Model):
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

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "timestamp"]),
            models.Index(fields=["content_type", "object_id", "timestamp"]),
            models.Index(fields=["source", "timestamp"]),
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
    ):
        subscriber = None
        if subscriber_id:
            try:
                subscriber = Subscriber.objects.get(id=subscriber_id)
            except (Subscriber.DoesNotExist, MultipleObjectsReturned) as error:
                logger.warning("Unable to attach subscriber to analytics event: %s %s", error, subscriber_id)

        user_agent = ""
        automated = False
        session_key = ""
        if request is not None:
            user_agent = request.headers.get("user-agent", "")
            automated = is_probable_automated_event(request)
            session_key = request.session.session_key or ""

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
        )

    def __str__(self):
        label = self.get_event_type_display()
        if self.content_object:
            return f"{label}: {self.content_object}"
        return label
