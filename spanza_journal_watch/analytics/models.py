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
