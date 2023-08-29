import base64
import uuid

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.db import models
from django.template.loader import render_to_string
from django.urls import reverse

from spanza_journal_watch.submissions.models import Issue, Review
from spanza_journal_watch.utils.celerytasks import celery_resize_greyscale_contrast_image
from spanza_journal_watch.utils.modelmethods import name_image

from .tasks import send_newsletter


class ElementImage(models.Model):
    UPCHEVRON = "UP"
    DOWNCHEVRON = "DN"
    LOGO = "LO"
    OTHER = "OT"
    CHOICES = [
        (UPCHEVRON, "Up chevron"),
        (DOWNCHEVRON, "Down chevron"),
        (LOGO, "Logo"),
        (OTHER, "Other"),
    ]
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=2, choices=CHOICES, default=OTHER, unique=True)
    image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )

    @classmethod
    def _get_unique_image_url(cls, type):
        try:
            instance = cls.objects.get(type=type).image.url
        except cls.DoesNotExist:  # Still raises MultipleObjectsReturned
            instance = None
        return instance

    @classmethod
    def get_up_chevron_url(cls):
        return cls._get_unique_image_url(cls.UPCHEVRON)

    @classmethod
    def get_down_chevron_url(cls):
        return cls._get_unique_image_url(cls.DOWNCHEVRON)

    def save(self, *args, **kwargs):
        # Ensure only a single instance of each type is created
        if not self.pk:
            try:
                # Update existing instance
                instance = ElementImage.objects.get(type=self.type)
                self.pk = instance.pk
            except ElementImage.DoesNotExist:
                # Create new instance
                pass

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Logo(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    @classmethod
    def get_latest_logo(cls):
        return cls.objects.order_by("-modified").first()

    def __str__(self):
        return self.name


class Subscriber(models.Model):
    email = models.EmailField(max_length=255)
    subscribed = models.BooleanField(default=True)
    tester = models.BooleanField(default=False)
    bounced = models.BooleanField(default=False)
    complained = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    unsubscribe_token = models.CharField(max_length=64, blank=True, null=True)

    def generate_confirmation_email_html(self):
        template = "newsletter/email_confirmation.html"
        context = {"subscriber": self}
        return render_to_string(template, context)

    def generate_confirmation_email_txt(self):
        template = "newsletter/email_confirmation.txt"
        context = {"subscriber": self}
        return render_to_string(template, context)

    def generate_confirmation_email(self):
        body = self.generate_confirmation_email_txt()

        email = mail.EmailMultiAlternatives(
            subject="Journal Watch Subscription",
            body=body,
            to=[self.email],
        )
        email.attach_alternative(self.generate_confirmation_email_html(), "text/html")
        return email

    def generate_unsubscribe_token(self):
        r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
        return r_uuid.replace("=", "")

    def get_unsubscribe_link(self, absolute=True):
        path = reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": self.unsubscribe_token})
        if absolute:
            if settings.DEBUG:
                domain = "127.0.0.1:3000"
            else:
                domain = Site.objects.get_current().domain
            return f"https://{domain}{path}"
        return path

    @classmethod
    def get_valid_subscribers(cls):
        subscribers = Subscriber.objects.filter(
            bounced=False,
            complained=False,
            subscribed=True,
        )
        return subscribers

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            self.unsubscribe_token = self.generate_unsubscribe_token()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Subscriber: {self.email}"


class Newsletter(models.Model):
    subject = models.CharField(max_length=255)
    content_heading = models.CharField(
        max_length=255, verbose_name="Heading for the introductory content", blank=True, null=True
    )
    content = models.TextField(verbose_name="Introductory content paragraph")
    send_date = models.DateTimeField(blank=True, null=True)
    ready_to_send = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)
    is_test_sent = models.BooleanField(default=False)
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT)
    header_image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )
    header_image_processed = models.BooleanField(default=False, editable=False)
    logo = models.ForeignKey(Logo, on_delete=models.SET_NULL, default=Logo.get_latest_logo, blank=True, null=True)
    non_featured_review_count = models.PositiveIntegerField(default=5, blank=True, null=True)

    # Get issue content
    def get_featured_reviews(self, count=2):
        return Review.objects.filter(issues=self.issue, is_featured=True, active=True).order_by("?")[:count]

    def get_non_featured_reviews(self, count=None):
        return Review.objects.filter(issues=self.issue, is_featured=False, active=True).order_by("?")[:count]

    @staticmethod
    def get_domain():
        if settings.DEBUG:
            domain = "127.0.0.1:3000"
            return f"http://{domain}"
        domain = Site.objects.get_current().domain
        return f"https://{domain}"

    # Assemble emails
    def get_email_context(self):
        context = {
            "newsletter": self,
            "featured_reviews": self.get_featured_reviews(),
            "non_featured_reviews": self.get_non_featured_reviews(count=self.non_featured_review_count),
            "domain": Newsletter.get_domain(),
            "element": ElementImage,
        }
        return context

    def generate_html_content(self, context):
        template = "newsletter/email_newsletter.html"
        return render_to_string(template, context)

    def generate_txt_content(self, context):
        template = "newsletter/email_newsletter.txt"
        return render_to_string(template, context)

    def generate_emails(self, subscribers):
        emails = []
        context = self.get_email_context()
        for subscriber in subscribers:
            context["subscriber"] = subscriber
            email = mail.EmailMultiAlternatives(
                subject=self.subject,
                body=self.generate_txt_content(context),
                to=[subscriber.email],
            )
            email.attach_alternative(self.generate_html_content(context), "text/html")
            emails.append(email)
        return emails

    # Send emails
    def send_test_email(self):
        pass

    def send_email(self):
        pass

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.header_image and not self.header_image_processed:
            celery_resize_greyscale_contrast_image.delay(self.header_image.name)
            Newsletter.objects.filter(pk=self.pk).update(header_image_processed=True)

        if not (self.is_sent or self.is_test_sent):
            send_newsletter.apply_async((self.pk,), {"test_email": True}, countdown=1)
