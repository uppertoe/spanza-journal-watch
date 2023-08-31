from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.template.loader import render_to_string

from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.utils.functions import get_domain_url


class NewsletterOpen(models.Model):
    email_address = models.EmailField(max_length=254)
    newsletter = models.ForeignKey(Newsletter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def render_tracking_pixel(email, token):
        context = {"email": email, "token": token, "domain": get_domain_url()}
        template = "analytics/email_pixel.html"
        return render_to_string(template, context)

    @classmethod
    def get_between_timestamps(cls, newsletter, start_timestamp, end_timestamp):
        return cls.objects.filter(newsletter=newsletter, timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    def __str__(self):
        return self.email_address


class NewsletterClick(models.Model):
    email_address = models.EmailField(max_length=254)
    newsletter = models.ForeignKey(Newsletter, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def generate_tracking_link(email, token):
        """Redirects to the url immediately following this tag"""
        context = {"email": email, "token": token, "domain": get_domain_url()}
        template = "analytics/email_link.txt"
        return render_to_string(template, context)

    @classmethod
    def get_between_timestamps(cls, newsletter, start_timestamp, end_timestamp):
        return cls.objects.filter(newsletter=newsletter, timestamp__gte=start_timestamp, timestamp__lte=end_timestamp)

    def __str__(self):
        return self.email_address


class PageView(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    timestamp = models.DateTimeField(auto_now_add=True)

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

    def __str__(self):
        datetime = self.timestamp.strftime("%d/%m/%Y, %H:%M:%S")
        return f"{str(self.content_object)}: {datetime}"
