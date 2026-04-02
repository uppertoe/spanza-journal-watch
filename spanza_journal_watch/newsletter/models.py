import base64
import uuid
from urllib.parse import quote

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.db import models
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from spanza_journal_watch.analytics.utils import click_tracker
from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.submissions.models import Issue, Review
from spanza_journal_watch.utils.celerytasks import celery_resize_greyscale_contrast_image
from spanza_journal_watch.utils.functions import get_domain_url
from spanza_journal_watch.utils.modelmethods import name_image


class Subscriber(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="subscriber",
    )
    email = models.EmailField(max_length=255)
    subscribed = models.BooleanField(default=True)
    tester = models.BooleanField(default=False)
    bounced = models.BooleanField(default=False)
    complained = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    unsubscribe_token = models.CharField(max_length=64, blank=True, null=True)
    from_csv = models.ForeignKey(
        SubscriberCSV, on_delete=models.CASCADE, blank=True, null=True, verbose_name="Uploaded via CSV"
    )

    def get_email_context(self):
        domain = get_domain_url()
        image_domain = domain if settings.DEBUG else ""

        context = {
            "domain": domain,
            "image_domain": image_domain,
            "email_up_chevron_url": f"{domain}{static('images/email/chevron-up.png')}",
            "email_down_chevron_url": f"{domain}{static('images/email/chevron-down.png')}",
            "email_logo_url": f"{domain}{static('images/email/logo.png')}",
            "email_heading_url": f"{domain}{static('images/email/heading.png')}",
            "spanza_logo_url": f"{domain}{static('images/logo/spanza-logo-blue.png')}",
            "subscriber": self,
            "tracker": click_tracker(self.email),
        }

        return context

    @staticmethod
    def generate_confirmation_email_html(context):
        template = "newsletter/email_confirmation.html"
        return render_to_string(template, context)

    @staticmethod
    def generate_confirmation_email_txt(context):
        template = "newsletter/email_confirmation.txt"
        return render_to_string(template, context)

    def generate_confirmation_email(self):
        context = self.get_email_context()
        body = Subscriber.generate_confirmation_email_txt(context)
        html = Subscriber.generate_confirmation_email_html(context)
        headers = self.get_list_unsubscribe_headers()

        email = mail.EmailMultiAlternatives(
            subject="Journal Watch Subscription",
            body=body,
            from_email=settings.SUBSCRIBE_FROM_EMAIL,
            to=[self.email],
            headers=headers,
            reply_to=[settings.NEWSLETTER_REPLY_TO],
        )
        email.attach_alternative(html, "text/html")
        email.metadata = {"type": "subscription_confirmation"}
        email.tags = ["subscription-confirmation"]
        return email

    def generate_unsubscribe_token(self):
        r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
        return r_uuid.replace("=", "")

    def get_unsubscribe_link(self, absolute=True):
        path = reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": self.unsubscribe_token})
        if absolute:
            if settings.DEBUG:
                domain = "127.0.0.1:3000"
                return f"http://{domain}{path}"
            domain = Site.objects.get_current().domain
            return f"https://{domain}{path}"
        return path

    def get_list_unsubscribe_headers(self):
        one_click_url = self.get_unsubscribe_link(absolute=True)
        mailto = f"mailto:unsubscribe@journalwatch.org.au?subject={quote('unsubscribe')}"
        return {
            "List-Unsubscribe": f"<{mailto}>, <{one_click_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "List-Id": "SPANZA Journal Watch <newsletter.journalwatch.org.au>",
            "Precedence": "list",
            "Auto-Submitted": "auto-generated",
            "X-Auto-Response-Suppress": "All",
            "Feedback-ID": "journalwatch:newsletter:spanza",
        }

    @classmethod
    def get_valid_subscribers(cls, test_email=True):
        if test_email:
            subscribers = cls.objects.filter(tester=True)
        else:
            subscribers = cls.objects.filter(
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
    resend_enabled = models.BooleanField(default=False)
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT)
    header_image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )
    header_image_processed = models.BooleanField(default=False)
    non_featured_review_count = models.PositiveIntegerField(default=5, blank=True, null=True)
    email_token = models.CharField(max_length=64, default="", editable=False, unique=True)
    send_token = models.CharField(max_length=64, default="", editable=False, unique=True)
    emails_sent = models.PositiveIntegerField(default=0, editable=False)

    # Newsletter stats
    def get_stats_absolute_url(self):
        return reverse("backend:newsletter_stats_detail", kwargs={"pk": self.pk})

    # Setup token
    def generate_email_token(self):
        r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
        return r_uuid.replace("=", "")

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
        domain = Newsletter.get_domain()
        image_domain = domain if settings.DEBUG else ""

        context = {
            "newsletter": self,
            "featured_reviews": self.get_featured_reviews(),
            "non_featured_reviews": self.get_non_featured_reviews(count=self.non_featured_review_count),
            "domain": domain,
            "image_domain": image_domain,
            "email_up_chevron_url": f"{domain}{static('images/email/chevron-up.png')}",
            "email_down_chevron_url": f"{domain}{static('images/email/chevron-down.png')}",
            "email_logo_url": f"{domain}{static('images/email/logo.png')}",
            "email_heading_url": f"{domain}{static('images/email/heading.png')}",
            "spanza_logo_url": f"{domain}{static('images/logo/spanza-logo-blue.png')}",
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
        token = self.email_token
        from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen

        for subscriber in subscribers:
            context["subscriber"] = subscriber
            context["pixel"] = NewsletterOpen.render_tracking_pixel(subscriber.email, token)
            context["tracker"] = NewsletterClick.generate_tracking_link(subscriber.email, token)
            headers = subscriber.get_list_unsubscribe_headers()
            email = mail.EmailMultiAlternatives(
                subject=self.subject,
                body=self.generate_txt_content(context),
                from_email=settings.NEWSLETTER_FROM_EMAIL,
                to=[subscriber.email],
                headers=headers,
                reply_to=[settings.NEWSLETTER_REPLY_TO],
            )
            email.attach_alternative(self.generate_html_content(context), "text/html")

            # Attach token to identify instigating email for bounces/complaints
            email.metadata = {"email_token": self.email_token, "type": "newsletter"}
            email.tags = ["newsletter"]

            emails.append(email)
        return emails

    # Send emails
    def is_ready_to_send(self):
        return self.ready_to_send and self.is_test_sent and (not self.is_sent or self.resend_enabled)

    def send_test_email(self):
        pass

    def send_email(self):
        pass

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = (
                Newsletter.objects.filter(pk=self.pk)
                .values(
                    "subject",
                    "content_heading",
                    "content",
                    "issue_id",
                    "header_image",
                    "non_featured_review_count",
                )
                .first()
            )

        if not self.email_token:
            self.email_token = self.generate_email_token()

        if previous:
            old_header_image = previous["header_image"] or ""
            new_header_image = self.header_image.name if self.header_image else ""

            content_changed = any(
                [
                    previous["subject"] != self.subject,
                    previous["content_heading"] != self.content_heading,
                    previous["content"] != self.content,
                    previous["issue_id"] != self.issue_id,
                    previous["non_featured_review_count"] != self.non_featured_review_count,
                    old_header_image != new_header_image,
                ]
            )

            if content_changed:
                self.is_test_sent = False

        # Refresh send token on every save
        self.send_token = self.generate_email_token()

        super().save(*args, **kwargs)

        if self.header_image and not self.header_image_processed:
            celery_resize_greyscale_contrast_image.delay(self.header_image.name)
            Newsletter.objects.filter(pk=self.pk).update(header_image_processed=True)

    def __str__(self):
        return self.subject
