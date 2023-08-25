import base64
import uuid

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.db import models
from django.template.loader import render_to_string
from django.urls import reverse

from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.utils.modelmethods import name_font, name_image


class EmailFont(models.Model):
    TITLE = "TI"
    BODY = "BO"
    OTHER = "OT"
    TYPE_CHOICES = [
        (TITLE, "Title"),
        (BODY, "BODY"),
        (OTHER, "Other"),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=2, choices=TYPE_CHOICES, default=OTHER)
    font = models.FileField(
        upload_to=name_font,
        blank=True,
        null=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    @classmethod
    def get_latest_title(cls):
        return cls.filter(type=cls.TITLE).order_by("-modified").first()

    @classmethod
    def get_latest_body(cls):
        return cls.filter(type=cls.BODY).order_by("-modified").first()

    def __str__(self):
        return self.name


class EmailImage(models.Model):
    HEADER = "HE"
    LOGO = "LO"
    OTHER = "OT"
    TYPE_CHOICES = [
        (HEADER, "Header"),
        (LOGO, "Logo"),
        (OTHER, "Other"),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=2, choices=TYPE_CHOICES, default=OTHER)
    image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    @classmethod
    def get_latest_header(cls):
        return cls.filter(type=cls.HEADER).order_by("-modified").first()

    @classmethod
    def get_latest_logo(cls):
        return cls.filter(type=cls.LOGO).order_by("-modified").first()

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
    content = models.TextField()
    send_date = models.DateTimeField(blank=True, null=True)
    ready_to_send = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)
    is_test_sent = models.BooleanField(default=False)
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)

    # Email media
    title_font = models.ForeignKey(EmailFont, on_delete=models.CASCADE, default=EmailFont.get_latest_title)
    header_image = models.ForeignKey(EmailImage, on_delete=models.CASCADE, default=EmailImage.get_latest_header)

    def render_email(self, subscriber, template):
        context = {
            "newsletter": self,
            "issue": self.issue,
            "subscriber": subscriber,
        }
        return render_to_string(template, context)

    def generate_html_content(self, subscriber):
        template = "newsletter/email_newsletter.html"
        return self.render_email(subscriber, template)

    def generate_txt_content(self, subscriber):
        template = "newsletter/email_newsletter.txt"
        return self.render_email(subscriber, template)

    def generate_emails(self, subscribers):
        emails = []
        for subscriber in subscribers:
            email = mail.EmailMultiAlternatives(
                subject=self.subject,
                body=self.generate_txt_content(subscriber),
                to=[subscriber.email],
            )
            email.attach_alternative(self.generate_html_content(subscriber), "text/html")
            emails.append(email)
        return emails

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # send_newsletter.delay(self.pk)
