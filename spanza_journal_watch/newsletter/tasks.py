import base64
import logging
import uuid

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import send_mail
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone

from config.celery_app import app as celery_app

logger = logging.getLogger(__name__)


class NewsletterNotReadyToSendError(Exception):
    pass


@celery_app.task()
def send_newsletter_stats(newsletter_pk, subscriber_count, batch_count, recipient_email=None):
    from .models import Newsletter

    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    if recipient_email:
        recipients = [recipient_email]
    else:
        # Fallback: no sender identified, notify all staff.
        recipients = list(get_user_model().objects.filter(is_staff=True).values_list("email", flat=True))

    recipients = [r for r in recipients if r]
    if not recipients:
        return

    context = {
        "newsletter": newsletter,
        "subscriber_count": subscriber_count,
        "batch_count": batch_count,
    }
    template = "newsletter/email_newsletter_stats.txt"
    subject = "Journal Watch - Newsletter send statistics"
    body = render_to_string(template, context)

    for email in recipients:
        send_mail(subject, body, None, [email])


BATCH_SIZE = 50  # Adjust this batch size as needed


def _generate_token():
    r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
    return r_uuid.replace("=", "")


def get_subscriber_batches(subscriber_pks, batch_size):
    # Slice the materialized PK list directly. Re-querying and slicing an
    # unordered QuerySet lets Postgres return different rows per LIMIT/OFFSET
    # query, causing some subscribers to be duplicated and others skipped.
    for start in range(0, len(subscriber_pks), batch_size):
        yield subscriber_pks[start : start + batch_size]


@celery_app.task()
def send_newsletter_batch(newsletter_pk, subscriber_pks, test_email):
    # Get newsletter object
    from .models import Newsletter, Subscriber

    newsletter_queryset = Newsletter.objects.filter(pk=newsletter_pk)
    newsletter = newsletter_queryset.get()

    # Get subscribers queryset
    subscribers = Subscriber.objects.filter(pk__in=subscriber_pks)

    # Send emails for this batch of subscribers
    connection = mail.get_connection()
    messages = newsletter.generate_emails(subscribers)
    try:
        successful = connection.send_messages(messages)
    except Exception:
        logger.exception(
            "Newsletter batch send failed for newsletter %s (%d recipients)",
            newsletter_pk,
            len(subscriber_pks),
        )
        raise

    if not test_email:
        newsletter_queryset.update(emails_sent=F("emails_sent") + successful)


@celery_app.task()
def send_newsletter(newsletter_pk, sender_email=None):
    from .models import Newsletter, Subscriber

    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    # Informative pre-checks; the real guard is the atomic claim below.
    if not newsletter.is_test_sent:
        raise NewsletterNotReadyToSendError(f"Newsletter {newsletter} has not been test sent")
    if not newsletter.ready_to_send:
        raise NewsletterNotReadyToSendError(f"Newsletter {newsletter} not marked ready to send")

    # Atomic claim: a single conditional UPDATE either flips is_sent False→True
    # or consumes resend_enabled. Guarantees at-most-once dispatch even if two
    # send requests arrive concurrently.
    claimed = (
        Newsletter.objects.filter(pk=newsletter_pk)
        .filter(Q(is_sent=False) | Q(resend_enabled=True))
        .update(is_sent=True, resend_enabled=False, send_date=timezone.now())
    )
    if claimed == 0:
        raise NewsletterNotReadyToSendError(
            f"Newsletter {newsletter} already sent to {newsletter.emails_sent} recipients; sending aborted"
        )

    subscribers = Subscriber.get_valid_subscribers(test_email=False)
    subscriber_pks = list(subscribers.values_list("pk", flat=True))

    batch_count = 0
    for batch_pks in get_subscriber_batches(subscriber_pks, BATCH_SIZE):
        send_newsletter_batch.delay(newsletter_pk, batch_pks, False)
        batch_count += 1

    send_newsletter_stats.delay(newsletter_pk, len(subscriber_pks), batch_count, sender_email)


@celery_app.task()
def send_newsletter_test_email(newsletter_pk, recipient_email):
    from .models import Newsletter, Subscriber

    newsletter_queryset = Newsletter.objects.filter(pk=newsletter_pk)
    newsletter = newsletter_queryset.get()

    # Create a lightweight in-memory subscriber instance for rendering templates
    subscriber = Subscriber(email=recipient_email, unsubscribe_token=_generate_token())
    connection = mail.get_connection()
    messages = newsletter.generate_emails([subscriber])
    successful = connection.send_messages(messages)

    if successful:
        newsletter_queryset.update(is_test_sent=True)


@celery_app.task(bind=True, max_retries=3)
def send_confirmation_email(self, subscriber_pk):
    """Sends a single EmailMessage object"""

    from .models import Subscriber  # Avoid circular import

    try:
        subscriber = Subscriber.objects.get(pk=subscriber_pk)
        email = subscriber.generate_confirmation_email()
        email.send()
        logger.info("Sign-up email sent to %s", subscriber.email)
    except Subscriber.DoesNotExist as exc:
        raise self.retry(exc=exc, countdown=3 * 60)


@celery_app.task()
def reset_unsubscribe_token(subscriber_pk):
    """Compatibility shim for older flows/tests.

    Unsubscribe links now remain stable and idempotent, so no token reset work
    is required here.
    """

    return None
