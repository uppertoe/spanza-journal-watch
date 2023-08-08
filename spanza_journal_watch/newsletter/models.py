from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.db import models
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import salted_hmac

from spanza_journal_watch.submissions.models import Issue


class UnsubscribeTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, subscriber, timestamp):
        email = subscriber.email
        return salted_hmac(self.key_salt, email + str(timestamp)).hexdigest()


class Subscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed = models.BooleanField(default=True)
    unsubscribe_token = models.CharField(max_length=64, blank=True, null=True)

    @classmethod
    def subscribe(cls, email):
        subscriber, created = cls.objects.get_or_create(email=email, defaults={"is_subscribed": True})
        if not created:
            subscriber.is_subscribed = True
            subscriber.save()
        return subscriber

    def generate_unsubscribe_token(self):
        token_generator = UnsubscribeTokenGenerator()
        return token_generator.make_token(self)

    def get_unsubscribe_link(self):
        return reverse("unsubscribe", kwargs={"unsubscribe_token": self.unsubscribe_token})

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            self.unsubscribe_token = self.generate_unsubscribe_token()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class Newsletter(models.Model):
    subject = models.CharField(max_length=255)
    content = models.TextField()
    send_date = models.DateTimeField()
    ready_to_send = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)
    is_test_sent = models.BooleanField(default=False)
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)

    def generate_html_content(self, subscriber):
        template_name = "newsletter/email_newsletter"
        context = {
            "newsletter": self,
            "issue": self.issue,
            "subscriber": subscriber,
        }
        return render_to_string(template_name, context)

    def generate_emails(self):
        subscribers = Subscriber.objects.exlude(subscribed=False)
        emails = []
        for subscriber in subscribers:
            email = mail.EmailMultiAlternatives(
                subject=self.subject,
                body=self.content,
                to=[subscriber.email],
            )
            email.attach_alternative(self.generate_html_content(), "text/html")
            emails.append(email)
        return emails
