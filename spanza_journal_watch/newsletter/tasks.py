from django.core import mail

from config.celery_app import app as celery_app


class NewsletterNotReadyToSendError(Exception):
    pass


@celery_app.task()
def send_newsletter(newsletter_pk, test_email=True):
    """
    Send a newsletter to subscribers.

    The newsletter must be ready_to_send == True and is_test_sent == True

    Args:
        newsletter_pk (int): The primary key of the newsletter to send.
        test_email (bool, optional): Whether to send to test subscribers. Defaults to False.
    """

    from .models import Newsletter, Subscriber  # Avoid circular import

    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    if test_email:
        subscribers = Subscriber.objects.filter(tester=True)
        newsletter.update(is_test_sent=True)
    else:
        if not (newsletter.ready_to_send and newsletter.is_test_sent):
            raise NewsletterNotReadyToSendError("newsletter object not ready to send")

        subscribers = Subscriber.get_valid_subscribers()
        newsletter.update(is_sent=True)

    connection = mail.get_connection()
    messages = newsletter.generate_emails(subscribers)
    successful = connection.send_messages(messages)
    print(f"{successful} of {len(messages)} emails sent successfully")


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
