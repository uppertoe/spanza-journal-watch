from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import send_mail
from django.db.models import F
from django.template.loader import render_to_string

from config.celery_app import app as celery_app


class NewsletterNotReadyToSendError(Exception):
    pass


@celery_app.task()
def send_newsletter_stats(newsletter_pk, subscriber_count, batch_count):
    from .models import Newsletter

    newsletter = Newsletter.objects.get(pk=newsletter_pk)
    staff = get_user_model().objects.filter(is_staff=True)

    context = {
        "newsletter": newsletter,
        "subscriber_count": subscriber_count,
        "batch_count": batch_count,
    }
    template = "newsletter/email_newsletter_stats.txt"
    subject = "Journal Watch - Newsletter send statistics"
    body = render_to_string(template, context)

    for member in staff:
        send_mail(
            subject,
            body,
            None,
            [member.email],
        )


@celery_app.task()
def send_newsletter_distribution_link(newsletter_pk):
    from .models import Newsletter

    staff = get_user_model().objects.filter(is_staff=True)
    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    context = {
        "send_token": newsletter.send_token,
        "newsletter": newsletter,
        "domain": Newsletter.get_domain(),
    }
    template = "newsletter/email_newsletter_distribution_link.txt"
    subject = "Distribution link for SPANZA newsletter"
    body = render_to_string(template, context)

    for member in staff:
        send_mail(
            subject,
            body,
            None,
            [member.email],
        )


BATCH_SIZE = 50  # Adjust this batch size as needed


def get_subscriber_batches(subscriber_pks, batch_size):
    from .models import Subscriber

    subscribers = Subscriber.objects.filter(pk__in=subscriber_pks)

    num_subscribers = subscribers.count()

    # Ensure that we round up to the nearest batch
    num_batches = (num_subscribers + batch_size - 1) // batch_size

    # Generator which yields a sliced queryset
    for batch_number in range(num_batches):
        start_index = batch_number * batch_size
        end_index = min((batch_number + 1) * batch_size, num_subscribers)
        subscribers_batch = subscribers[start_index:end_index]
        yield subscribers_batch


@celery_app.task()
def send_newsletter_batch(newsletter_pk, subscriber_pks, test_email):
    # Get newsletter object
    from .models import Newsletter, Subscriber

    newsletter_queryset = Newsletter.objects.filter(pk=newsletter_pk)
    newsletter = newsletter_queryset[0]

    # Get subscribers queryset
    subscribers = Subscriber.objects.filter(pk__in=subscriber_pks)

    # Send emails for this batch of subscribers
    connection = mail.get_connection()
    messages = newsletter.generate_emails(subscribers)
    connection.send_messages(messages)

    if not test_email:
        newsletter_queryset.update(emails_sent=F("emails_sent") + 1)


@celery_app.task()
def send_newsletter(newsletter_pk, test_email=True):
    # Get models
    from .models import Newsletter, Subscriber

    subscribers = Subscriber.get_valid_subscribers(test_email=test_email)

    newsletter_queryset = Newsletter.objects.filter(pk=newsletter_pk)

    # Serialise queryset for Celery
    subscriber_pks = list(subscribers.values_list("pk", flat=True))

    batch_count = 0

    # Send emails to each batch of subscribers
    for subscribers_batch in get_subscriber_batches(subscriber_pks, BATCH_SIZE):
        batch_pks = list(subscribers_batch.values_list("pk", flat=True))  # Ensure serialisable for Celery
        send_newsletter_batch.delay(newsletter_pk, batch_pks, test_email)
        batch_count += 1

    # Mark as sent after all emails are processed
    newsletter_queryset.update(is_test_sent=True) if test_email else newsletter_queryset.update(is_sent=True)

    if test_email:
        # Send the distribution link to the administrator
        send_newsletter_distribution_link.delay(newsletter_pk)
    else:
        # Send statistics of the operation to the administrator
        subscriber_count = len(subscriber_pks)
        send_newsletter_stats.delay(newsletter_pk, subscriber_count, batch_count)


@celery_app.task(bind=True, max_retries=3)
def send_confirmation_email(self, subscriber_pk):
    """Sends a single EmailMessage object"""

    from .models import Subscriber  # Avoid circular import

    try:
        subscriber = Subscriber.objects.get(pk=subscriber_pk)
        email = subscriber.generate_confirmation_email()
        email.send()
        print(f"Sign-up email sent to {subscriber.email}")
    except Subscriber.DoesNotExist as exc:
        raise self.retry(exc=exc, countdown=3 * 60)


@celery_app.task()
def reset_unsubscribe_token(subscriber_pk):
    from .models import Subscriber

    subscriber = Subscriber.objects.get(pk=subscriber_pk)
    subscriber.unsubscribe_token = ""
    subscriber.save()
