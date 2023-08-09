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
