from django.conf import settings

from spanza_journal_watch.newsletter.models import Newsletter

from .models import Subscriber


def _get_subscriber(email):
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        subscriber = None
    return subscriber


def handle_bounce(event):
    # Soft bounces are not removed from mailing list
    bounce_type, bounce_subtype = event.description.strip().lower().split(":", 1)
    if bounce_type == "transient":
        return print(f"Transient bounce for email {event.recipient} (subtype {bounce_subtype}); kept on mailing list")

    subscriber = _get_subscriber(event.recipient)
    if subscriber:
        subscriber.bounced = True
        subscriber.save()
        print(f"Email to {subscriber} bounced; removed from mailing list")
    else:
        print(f"No subscriber found for bounced email: {subscriber}")


def handle_complaint(event):
    subscriber = _get_subscriber(event.recipient)
    if subscriber:
        subscriber.complained = True
        subscriber.save()
        print(f"Email to {subscriber} complained; removed from mailing list")
    else:
        print(f"No subscriber found for complaint: {subscriber}")


def get_metadata(event):
    token = event.metadata.get("email_token")
    type = event.metadata.get("type")
    if token:
        if type == "newsletter":
            newsletter = Newsletter.objects.filter(email_token=token)
            print(f"Response to newsletter: {newsletter}")


if not settings.DEBUG:  # Anymail only available in production
    from anymail.signals import tracking
    from django.dispatch import receiver

    @receiver(tracking)  # add weak=False if inside some other function/class
    def handle_tracking(sender, event, esp_name, **kwargs):
        if event.event_type == "bounced":
            handle_bounce(event)

        if event.event_type == "complained":
            handle_complaint(event)

        get_metadata(event)
