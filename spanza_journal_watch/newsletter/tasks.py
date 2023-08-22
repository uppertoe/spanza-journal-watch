from django.core import mail

from config.celery_app import app as celery_app


@celery_app.task()
def send_newsletter(newsletter_pk):
    """Takes a list of EmailMessage objects and sends them"""

    from .models import Newsletter  # Avoid circular import

    newsletter = Newsletter.objects.get(pk=newsletter_pk)

    connection = mail.get_connection()
    messages = newsletter.generate_emails()
    successful = connection.send_messages(messages)
    print(f"{successful} of {len(messages)} emails sent successfully")


@celery_app.task()
def send_confirmation_email(subscriber_pk):
    """Sends a single EmailMessage object"""

    from .models import Subscriber  # Avoid circular import

    subscriber = Subscriber.objects.get(pk=subscriber_pk)
    email = subscriber.generate_confirmation_email()
    email.send()
    print(f"Sign-up email sent to {subscriber.email}")


@celery_app.task()
def reset_unsubscribe_token(subscriber_pk):
    from .models import Subscriber

    subscriber = Subscriber.objects.get(pk=subscriber_pk)
    subscriber.unsubscribe_token = ""
    subscriber.save()
