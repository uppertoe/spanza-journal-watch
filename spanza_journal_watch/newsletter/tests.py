from types import SimpleNamespace

import pytest
from django.test import override_settings

from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.newsletter.signals import (
    handle_bounce,
    handle_complaint,
    handle_subscription,
    handle_unsubscribed,
)


@pytest.mark.django_db
class TestNewsletterTrackingSignals:
    def test_permanent_bounce_marks_subscriber_bounced_and_unsubscribed(self):
        subscriber = Subscriber.objects.create(email="bounce@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Permanent:General",
            recipient="bounce@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )

        handle_bounce(event)

        subscriber.refresh_from_db()
        assert subscriber.bounced is True
        assert subscriber.subscribed is False

    def test_transient_bounce_keeps_subscriber_active(self):
        subscriber = Subscriber.objects.create(email="soft@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Transient:MailboxFull",
            recipient="soft@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )

        handle_bounce(event)

        subscriber.refresh_from_db()
        assert subscriber.bounced is False
        assert subscriber.subscribed is True

    def test_ses_payload_permanent_bounce_overrides_description(self):
        subscriber = Subscriber.objects.create(email="sesbounce@example.test", subscribed=True)
        event = SimpleNamespace(
            description="",
            recipient="",
            recipients=[],
            esp_event={
                "notificationType": "Bounce",
                "bounce": {
                    "bounceType": "Permanent",
                    "bounceSubType": "Suppressed",
                    "bouncedRecipients": [{"emailAddress": "sesbounce@example.test"}],
                },
            },
            metadata={},
        )

        handle_bounce(event)

        subscriber.refresh_from_db()
        assert subscriber.bounced is True
        assert subscriber.subscribed is False

    def test_complaint_marks_complained_and_unsubscribed(self):
        subscriber = Subscriber.objects.create(email="complaint@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Complaint",
            recipient="complaint@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )

        handle_complaint(event)

        subscriber.refresh_from_db()
        assert subscriber.complained is True
        assert subscriber.subscribed is False

    def test_unsubscribed_event_marks_subscriber_unsubscribed(self):
        subscriber = Subscriber.objects.create(email="unsub@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Unsubscribed",
            recipient="unsub@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )

        handle_unsubscribed(event)

        subscriber.refresh_from_db()
        assert subscriber.subscribed is False

    def test_subscription_event_opt_out_marks_subscriber_unsubscribed(self):
        subscriber = Subscriber.objects.create(email="subscription-optout@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Subscription",
            recipient="",
            recipients=[],
            esp_event={
                "eventType": "Subscription",
                "mail": {"destination": ["subscription-optout@example.test"]},
                "subscription": {
                    "newTopicPreferences": {
                        "unsubscribeAll": True,
                    }
                },
            },
            metadata={},
            event_type="subscription",
        )

        handle_subscription(event)

        subscriber.refresh_from_db()
        assert subscriber.subscribed is False

    def test_get_valid_subscribers_excludes_unsubscribed_complained_and_bounced(self):
        keep = Subscriber.objects.create(email="keep@example.test", subscribed=True, complained=False, bounced=False)
        unsubscribed = Subscriber.objects.create(
            email="unsubscribed@example.test", subscribed=False, complained=False, bounced=False
        )
        complained = Subscriber.objects.create(
            email="complained@example.test", subscribed=True, complained=True, bounced=False
        )
        bounced = Subscriber.objects.create(
            email="bounced@example.test", subscribed=True, complained=False, bounced=True
        )

        valid = list(Subscriber.get_valid_subscribers(test_email=False).order_by("email"))

        assert keep in valid
        assert unsubscribed not in valid
        assert complained not in valid
        assert bounced not in valid


@pytest.mark.django_db
@override_settings(
    SUBSCRIBE_FROM_EMAIL="Journal Watch <subscribe@example.test>",
    NEWSLETTER_REPLY_TO="queries@example.test",
)
def test_confirmation_email_uses_reply_to_and_metadata():
    subscriber = Subscriber.objects.create(email="subscriber@example.test", subscribed=True)

    message = subscriber.generate_confirmation_email()

    assert message.from_email == "Journal Watch <subscribe@example.test>"
    assert message.reply_to == ["queries@example.test"]
    assert message.metadata == {"type": "subscription_confirmation"}
    assert message.tags == ["subscription-confirmation"]
