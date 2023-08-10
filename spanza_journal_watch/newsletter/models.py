import base64
import uuid

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.db import models
from django.template.loader import render_to_string
from django.urls import reverse

from spanza_journal_watch.submissions.models import Issue

# from .tasks import send_newsletter


class Subscriber(models.Model):
    email = models.EmailField(max_length=255)
    subscribed = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    unsubscribe_token = models.CharField(max_length=64, blank=True, null=True)

    def generate_confirmation_email_html(self):
        template_name = "newsletter/email_confirmation.html"
        context = {"subscriber": self}
        return render_to_string(template_name, context)

    def generate_confirmation_email(self):
        body = f"""
        Thank you for subscribing to SPANZA Journal Watch.

        You will receive updates with each issue of Journal Watch,
        which is published every two months.

        You are receiving this email because you subscribed at
        journalwatch.org.au using the email {self.email}

        You can unsubscribe at any time by visiting the following address:
        {self.get_unsubscribe_link()}

        Sincerely,
        The SPANZA Journal Watch team
        """
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

    def generate_html_content(self, subscriber):
        template_name = "newsletter/email_newsletter.html"
        context = {
            "newsletter": self,
            "issue": self.issue,
            "subscriber": subscriber,
        }
        return render_to_string(template_name, context)

    def generate_emails(self):
        subscribers = Subscriber.objects.exclude(subscribed=False)
        emails = []
        for subscriber in subscribers:
            email = mail.EmailMultiAlternatives(
                subject=self.subject,
                body=self.content,
                to=[subscriber.email],
            )
            email.attach_alternative(self.generate_html_content(subscriber), "text/html")
            emails.append(email)
        return emails

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # send_newsletter.delay(self.pk)
