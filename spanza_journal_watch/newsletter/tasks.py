import base64
import datetime
import logging
import uuid

from celery import chord
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import send_mail
from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.utils import timezone

from config.celery_app import app as celery_app

logger = logging.getLogger(__name__)


class NewsletterNotReadyToSendError(Exception):
    pass


@celery_app.task(ignore_result=True)
def send_newsletter_stats(
    newsletter_pk,
    subscriber_count,
    batch_count,
    recipient_email=None,
    already_delivered=0,
    run_started_at=None,
):
    """Email the sender a summary of one send run.

    ``send_newsletter`` queues this as the callback of the batch chord, so it runs once every batch
    has finished and ``run_started_at`` (ISO timestamp of the claim) scopes the count to this run.
    """
    from .models import Newsletter, NewsletterDelivery

    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    if recipient_email:
        recipients = [recipient_email]
    else:
        # Fallback: no sender identified, notify all staff.
        recipients = list(get_user_model().objects.filter(is_staff=True).values_list("email", flat=True))

    recipients = [r for r in recipients if r]
    if not recipients:
        return

    deliveries = NewsletterDelivery.objects.filter(newsletter_id=newsletter_pk)
    if run_started_at:
        deliveries = deliveries.filter(sent_at__gte=datetime.datetime.fromisoformat(run_started_at))

    delivered_this_run = deliveries.count()
    context = {
        "newsletter": newsletter,
        "subscriber_count": subscriber_count,
        "batch_count": batch_count,
        "already_delivered": already_delivered,
        "delivered_this_run": delivered_this_run,
        "not_sent": max(subscriber_count - delivered_this_run, 0),
    }
    template = "newsletter/email_newsletter_stats.txt"
    subject = "Journal Watch - Newsletter send statistics"
    body = render_to_string(template, context)

    for email in recipients:
        send_mail(subject, body, None, [email])


@celery_app.task(ignore_result=True)
def send_newsletter_stats_after_failure(request, exc, traceback, *args, **kwargs):
    """Error callback for the batch chord: a failed batch still produces the summary email."""
    send_newsletter_stats(*args, **kwargs)


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


# Keeps its result: the stats chord counts finished batches through the result backend.
# Its arguments are primary keys only.
@celery_app.task()
def send_newsletter_batch(newsletter_pk, subscriber_pks, test_email):
    from .models import Newsletter, NewsletterDelivery, Subscriber

    newsletter_queryset = Newsletter.objects.filter(pk=newsletter_pk)
    newsletter = newsletter_queryset.get()

    subscribers = Subscriber.objects.filter(pk__in=subscriber_pks)
    if not test_email:
        # A batch re-run after a lost queue must not email anyone twice.
        subscribers = subscribers.exclude(newsletter_deliveries__newsletter_id=newsletter_pk)
    subscribers = list(subscribers)

    connection = mail.get_connection()
    messages = newsletter.generate_emails(subscribers)
    successful = 0
    try:
        for subscriber, message in zip(subscribers, messages, strict=True):
            sent = connection.send_messages([message]) or 0
            if sent and not test_email:
                NewsletterDelivery.objects.get_or_create(newsletter_id=newsletter_pk, subscriber=subscriber)
            successful += sent
    except Exception:
        logger.exception(
            "Newsletter batch send failed for newsletter %s after %d of %d recipients",
            newsletter_pk,
            successful,
            len(subscribers),
        )
        raise
    finally:
        if successful and not test_email:
            newsletter_queryset.update(emails_sent=F("emails_sent") + successful)


@celery_app.task(ignore_result=True)
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
    run_started_at = timezone.now()
    claimed = (
        Newsletter.objects.filter(pk=newsletter_pk)
        .filter(Q(is_sent=False) | Q(resend_enabled=True))
        # A resend keeps the first send_date, so the analytics windows do not move.
        .update(
            is_sent=True,
            resend_enabled=False,
            send_date=Coalesce(F("send_date"), Value(run_started_at)),
            scheduled_send_at=None,
        )
    )
    if claimed == 0:
        raise NewsletterNotReadyToSendError(
            f"Newsletter {newsletter} already sent to {newsletter.emails_sent} recipients; sending aborted"
        )

    subscribers = Subscriber.get_valid_subscribers(test_email=False)
    # A resend reaches only subscribers the newsletter has not already gone to.
    already_delivered = subscribers.filter(newsletter_deliveries__newsletter_id=newsletter_pk).count()
    subscriber_pks = list(
        subscribers.exclude(newsletter_deliveries__newsletter_id=newsletter_pk).values_list("pk", flat=True)
    )

    batches = [
        send_newsletter_batch.si(newsletter_pk, batch_pks, False)
        for batch_pks in get_subscriber_batches(subscriber_pks, BATCH_SIZE)
    ]
    stats_args = (newsletter_pk, len(subscriber_pks), len(batches), sender_email)
    stats_kwargs = {"already_delivered": already_delivered, "run_started_at": run_started_at.isoformat()}
    if not batches:
        send_newsletter_stats.delay(*stats_args, **stats_kwargs)
        return

    # The stats email is the chord callback, so it reports only once every batch has finished.
    # If a batch fails the chord callback never runs, so the error callback sends it instead.
    callback = send_newsletter_stats.si(*stats_args, **stats_kwargs)
    callback.link_error(send_newsletter_stats_after_failure.s(*stats_args, **stats_kwargs))
    chord(batches)(callback)


@celery_app.task(ignore_result=True)
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


@celery_app.task(bind=True, max_retries=3, ignore_result=True)
def send_confirmation_email(self, subscriber_pk):
    """Sends a single EmailMessage object"""

    from .models import Subscriber  # Avoid circular import

    try:
        subscriber = Subscriber.objects.get(pk=subscriber_pk)
        email = subscriber.generate_confirmation_email()
        email.send()
        logger.info("Sign-up email sent to subscriber %s", subscriber.pk)
    except Subscriber.DoesNotExist as exc:
        raise self.retry(exc=exc, countdown=3 * 60)


@celery_app.task(ignore_result=True)
def send_subscription_request_email(email, code):
    """Send the double opt-in link, carrying ``code``, to an address someone entered in a subscribe form."""
    from .confirmation import build_confirmation_request_email

    build_confirmation_request_email(email, code).send()
    logger.info("Subscription confirmation request sent")


@celery_app.task()
def reset_unsubscribe_token(subscriber_pk):
    """Compatibility shim for older flows/tests.

    Unsubscribe links now remain stable and idempotent, so no token reset work
    is required here.
    """

    return None


@celery_app.task()
def send_scheduled_newsletters():
    """Send each newsletter whose scheduled time has passed. Runs every minute from beat."""
    from .models import Newsletter

    for newsletter in Newsletter.objects.filter(scheduled_send_at__lte=timezone.now()):
        # Clearing the schedule claims it, so an overlapping run cannot send twice.
        claimed = Newsletter.objects.filter(pk=newsletter.pk, scheduled_send_at=newsletter.scheduled_send_at).update(
            scheduled_send_at=None
        )
        if not claimed:
            continue
        if newsletter.is_ready_to_send():
            send_newsletter.delay(newsletter.pk, sender_email=newsletter.scheduled_by_email or None)
        else:
            logger.warning("Scheduled newsletter %s was not ready to send; schedule cleared", newsletter.pk)
