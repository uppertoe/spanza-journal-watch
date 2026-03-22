import json
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


def _parse_json_if_needed(payload):
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_ses_event_payload(event):
    payload = _parse_json_if_needed(getattr(event, "esp_event", {}) or {})
    if not payload:
        return {}

    # SNS wrapper usually stores the SES notification in the Message field.
    if "Message" in payload:
        inner = _parse_json_if_needed(payload.get("Message"))
        if inner:
            return inner

    return payload


def _extract_recipients(event):
    recipients = []

    recipient = (getattr(event, "recipient", "") or "").strip().lower()
    if recipient:
        recipients.append(recipient)

    for value in getattr(event, "recipients", []) or []:
        email = (value or "").strip().lower()
        if email:
            recipients.append(email)

    payload = _extract_ses_event_payload(event)
    for value in (payload.get("mail", {}) or {}).get("destination", []) or []:
        email = (value or "").strip().lower()
        if email:
            recipients.append(email)

    for item in (payload.get("bounce", {}) or {}).get("bouncedRecipients", []) or []:
        email = (item.get("emailAddress") or "").strip().lower()
        if email:
            recipients.append(email)
    for item in (payload.get("complaint", {}) or {}).get("complainedRecipients", []) or []:
        email = (item.get("emailAddress") or "").strip().lower()
        if email:
            recipients.append(email)

    # Preserve ordering while deduplicating.
    return list(dict.fromkeys(recipients))


def _extract_bounce_type_and_subtype(event):
    payload = _extract_ses_event_payload(event)
    bounce = payload.get("bounce") if isinstance(payload, dict) else None
    if isinstance(bounce, dict):
        bounce_type = (bounce.get("bounceType") or "").strip().lower()
        bounce_subtype = (bounce.get("bounceSubType") or "").strip().lower()
        if bounce_type:
            return bounce_type, bounce_subtype

    description = (getattr(event, "description", "") or "").strip().lower()
    if ":" in description:
        bounce_type, bounce_subtype = description.split(":", 1)
        return bounce_type.strip(), bounce_subtype.strip()

    if description:
        return description, ""

    return "", ""


def handle_bounce(event):
    bounce_type, bounce_subtype = _extract_bounce_type_and_subtype(event)

    # Only permanent bounces should suppress future sends.
    if bounce_type != "permanent":
        logger.info(
            "Transient bounce for email %s (subtype %s); kept on mailing list",
            ", ".join(_extract_recipients(event)) or "(unknown)",
            bounce_subtype,
        )
        return

    recipients = _extract_recipients(event)
    if not recipients:
        logger.warning("Permanent bounce received with no recipient details.")
        return

    for recipient in recipients:
        subscriber = _get_subscriber(recipient)
        if subscriber:
            subscriber.bounced = True
            subscriber.subscribed = False
            subscriber.save(update_fields=["bounced", "subscribed", "modified"])
            logger.info(
                "Email to %s bounced permanently (%s); removed from mailing list",
                subscriber,
                bounce_subtype,
            )
        else:
            logger.warning("No subscriber found for permanent bounced email %s (%s)", recipient, bounce_subtype)


def handle_complaint(event):
    recipients = _extract_recipients(event)
    if not recipients:
        logger.warning("Complaint event received with no recipient details.")
        return

    for recipient in recipients:
        subscriber = _get_subscriber(recipient)
        if subscriber:
            subscriber.complained = True
            subscriber.subscribed = False
            subscriber.save(update_fields=["complained", "subscribed", "modified"])
            logger.info("Email to %s complained; removed from mailing list", subscriber)
        else:
            logger.warning("No subscriber found for complaint event (%s)", recipient)


def handle_unsubscribed(event):
    recipients = _extract_recipients(event)
    if not recipients:
        logger.warning("Unsubscribe event received with no recipient details.")
        return

    for recipient in recipients:
        subscriber = _get_subscriber(recipient)
        if subscriber:
            subscriber.subscribed = False
            subscriber.save(update_fields=["subscribed", "modified"])
            logger.info("Email to %s unsubscribed via ESP event.", subscriber)
        else:
            logger.warning("No subscriber found for unsubscribe event (%s)", recipient)


def _is_subscription_opt_out(event):
    payload = _extract_ses_event_payload(event)
    subscription = payload.get("subscription") if isinstance(payload, dict) else None
    if not isinstance(subscription, dict):
        return False

    new_preferences = subscription.get("newTopicPreferences") or {}
    if not isinstance(new_preferences, dict):
        return False

    if bool(new_preferences.get("unsubscribeAll")):
        return True

    for _, value in new_preferences.items():
        if not isinstance(value, dict):
            continue
        status = (value.get("subscriptionStatus") or value.get("topicSubscriptionStatus") or "").strip().lower()
        if status == "optout":
            return True

    return False


def handle_subscription(event):
    if _is_subscription_opt_out(event):
        handle_unsubscribed(event)
        return

    logger.info("Subscription event received but no OptOut state detected; no local subscriber status change.")


def get_metadata(event):
    metadata = getattr(event, "metadata", {}) or {}
    token = metadata.get("email_token")
    type = metadata.get("type")
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
            event_type = (getattr(event, "event_type", "") or "").strip().lower()

            if event_type == "bounced":
                handle_bounce(event)

            if event_type == "complained":
                handle_complaint(event)

            if event_type == "unsubscribed":
                handle_unsubscribed(event)

            if event_type == "subscription":
                handle_subscription(event)

            get_metadata(event)
