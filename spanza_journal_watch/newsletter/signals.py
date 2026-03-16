import logging

from django.conf import settings

from spanza_journal_watch.newsletter.models import Newsletter

from .models import Subscriber

logger = logging.getLogger(__name__)


def _get_subscriber(email):
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        subscriber = None
    return subscriber


def handle_bounce(event):
    # Soft bounces are not removed from mailing list
    bounce_type, bounce_subtype = event.description.strip().lower().split(":", 1)
    if not bounce_type == "permanent":  # May be transient or undetermined
        logger.info(
            "Transient bounce for email %s (subtype %s); kept on mailing list",
            event.recipient,
            bounce_subtype,
        )
        return

    subscriber = _get_subscriber(event.recipient)
    if subscriber:
        subscriber.bounced = True
        subscriber.save()
        logger.info(
            "Email to %s bounced due to %s; removed from mailing list",
            subscriber,
            bounce_subtype,
        )
    else:
        logger.warning("No subscriber found for bounced (%s) email", bounce_subtype)


def handle_complaint(event):
    subscriber = _get_subscriber(event.recipient)
    if subscriber:
        subscriber.complained = True
        subscriber.save()
        logger.info("Email to %s complained; removed from mailing list", subscriber)
    else:
        logger.warning("No subscriber found for complaint event")


def get_metadata(event):
    token = event.metadata.get("email_token")
    type = event.metadata.get("type")
    if token:
        if type == "newsletter":
            newsletter = Newsletter.objects.filter(email_token=token)
            logger.info("Response to newsletter query: %s", newsletter)


if not settings.DEBUG:  # Anymail only available in production
    try:
        from anymail.signals import tracking  # type: ignore[import-not-found]
        from django.dispatch import receiver
    except ModuleNotFoundError:
        tracking = None

    if tracking:

        @receiver(tracking)  # add weak=False if inside some other function/class
        def handle_tracking(sender, event, esp_name, **kwargs):
            if event.event_type == "bounced":
                handle_bounce(event)

            if event.event_type == "complained":
                handle_complaint(event)

            get_metadata(event)
