"""Unsubscribe requests that arrive by email through the List-Unsubscribe mailto address.

Apple Mail and some other clients use the mailto option rather than the one-click URL.
"""

import logging
from email.utils import parseaddr

from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)

UNSUBSCRIBE_ADDRESS = "unsubscribe@journalwatch.org.au"


def _address(value):
    return parseaddr(str(value or ""))[1].strip().lower()


def _sender_is_authenticated(from_address, envelope_sender, receipt):
    """True when SES vouches that the From address is genuine, so a forged request cannot unsubscribe someone."""
    if (receipt.get("dmarcVerdict") or {}).get("status", "").upper() == "PASS":
        return True
    spf_passed = (receipt.get("spfVerdict") or {}).get("status", "").upper() == "PASS"
    return spf_passed and from_address == _address(envelope_sender)


def handle_unsubscribe_email(message, esp_event):
    """Unsubscribe the sender of an email to the unsubscribe address.

    Returns True when the email was handled here. Returns False when it should go to the inbox
    instead: it was sent elsewhere, its sender could not be authenticated, or no subscriber matches.
    """
    from .models import Subscriber

    if _address(message.envelope_recipient) != UNSUBSCRIBE_ADDRESS:
        return False

    from_address = _address(message.from_email)
    receipt = (esp_event or {}).get("receipt") or {}
    if not from_address or not _sender_is_authenticated(from_address, message.envelope_sender, receipt):
        logger.warning("Unsubscribe email could not be authenticated; left in the inbox")
        return False

    subscribers = Subscriber.by_email(from_address)
    if not subscribers.exists():
        logger.info("Unsubscribe email matched no subscriber; left in the inbox")
        return False

    now = timezone.now()
    # A bulk update skips Subscriber.save, so unsubscribed_at is kept in step here.
    updated = subscribers.filter(subscribed=True).update(
        subscribed=False,
        unsubscribed_at=Coalesce(F("unsubscribed_at"), Value(now)),
        modified=now,
    )
    logger.info("Unsubscribed %d subscriber record(s) by email request", updated)
    return True
