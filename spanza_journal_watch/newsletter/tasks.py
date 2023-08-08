from django.core import mail

from config.celery_app import app as celery_app


@celery_app.task()
def send_newsletter(newsletter):
    # Takes a list of EmailMessage objects and sends them
    connection = mail.get_connection()
    messages = newsletter.generate_emails()
    successful = connection.send_messages(messages)
    print(f"{successful} of {len(messages)} sent successfully")
