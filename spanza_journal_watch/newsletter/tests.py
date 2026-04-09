from types import SimpleNamespace

import pytest
from django.test import Client, override_settings
from django.urls import reverse

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


@pytest.mark.django_db
class TestNewsletterSignalEdgeCases:
    """Edge cases for SES event parsing and recipient extraction."""

    def test_ses_sns_wrapper_message_parsed(self):
        """When SES sends via SNS, the real payload is inside a 'Message' key as JSON string."""
        import json

        subscriber = Subscriber.objects.create(email="sns@example.test", subscribed=True)
        inner_payload = json.dumps(
            {
                "notificationType": "Bounce",
                "bounce": {
                    "bounceType": "Permanent",
                    "bounceSubType": "General",
                    "bouncedRecipients": [{"emailAddress": "sns@example.test"}],
                },
            }
        )
        event = SimpleNamespace(
            description="",
            recipient="",
            recipients=[],
            esp_event={"Message": inner_payload},
            metadata={},
        )

        handle_bounce(event)

        subscriber.refresh_from_db()
        assert subscriber.bounced is True
        assert subscriber.subscribed is False

    def test_bounce_with_no_matching_subscriber_does_not_raise(self):
        event = SimpleNamespace(
            description="Permanent:General",
            recipient="nonexistent@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )
        # Should not raise
        handle_bounce(event)

    def test_complaint_with_no_matching_subscriber_does_not_raise(self):
        event = SimpleNamespace(
            description="Complaint",
            recipient="ghost@example.test",
            recipients=[],
            esp_event={},
            metadata={},
        )
        handle_complaint(event)

    def test_bounce_with_no_recipients_does_not_raise(self):
        event = SimpleNamespace(
            description="Permanent:General",
            recipient="",
            recipients=[],
            esp_event={},
            metadata={},
        )
        handle_bounce(event)

    def test_subscription_topic_level_optout(self):
        """Test _is_subscription_opt_out with topic-level preferences."""
        subscriber = Subscriber.objects.create(email="topic-optout@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Subscription",
            recipient="",
            recipients=[],
            esp_event={
                "eventType": "Subscription",
                "mail": {"destination": ["topic-optout@example.test"]},
                "subscription": {
                    "newTopicPreferences": {
                        "newsletter": {
                            "subscriptionStatus": "OptOut",
                        }
                    }
                },
            },
            metadata={},
            event_type="subscription",
        )

        handle_subscription(event)

        subscriber.refresh_from_db()
        assert subscriber.subscribed is False

    def test_subscription_event_without_optout_keeps_subscriber(self):
        subscriber = Subscriber.objects.create(email="keep@example.test", subscribed=True)
        event = SimpleNamespace(
            description="Subscription",
            recipient="",
            recipients=[],
            esp_event={
                "eventType": "Subscription",
                "mail": {"destination": ["keep@example.test"]},
                "subscription": {
                    "newTopicPreferences": {
                        "newsletter": {
                            "subscriptionStatus": "OptIn",
                        }
                    }
                },
            },
            metadata={},
            event_type="subscription",
        )

        handle_subscription(event)

        subscriber.refresh_from_db()
        assert subscriber.subscribed is True

    def test_recipients_deduplicated(self):
        from spanza_journal_watch.newsletter.signals import _extract_recipients

        event = SimpleNamespace(
            recipient="dupe@example.test",
            recipients=["dupe@example.test", "other@example.test"],
            esp_event={},
        )
        result = _extract_recipients(event)
        assert result.count("dupe@example.test") == 1
        assert "other@example.test" in result

    def test_recipients_normalized_to_lowercase(self):
        from spanza_journal_watch.newsletter.signals import _extract_recipients

        event = SimpleNamespace(
            recipient="UPPER@Example.Test",
            recipients=[],
            esp_event={},
        )
        result = _extract_recipients(event)
        assert result == ["upper@example.test"]

    def test_esp_event_as_string_parsed(self):
        """esp_event can be a JSON string rather than a dict."""
        import json

        from spanza_journal_watch.newsletter.signals import _extract_ses_event_payload

        payload = json.dumps({"bounce": {"bounceType": "Permanent"}})
        event = SimpleNamespace(esp_event=payload)
        result = _extract_ses_event_payload(event)
        assert result["bounce"]["bounceType"] == "Permanent"


@pytest.mark.django_db
class TestUnsubscribeViews:
    def test_unsubscribe_get_shows_confirmation_page(self):
        subscriber = Subscriber.objects.create(email="reader@example.test", subscribed=True)
        client = Client()

        response = client.get(reverse("newsletter:unsubscribe", args=[subscriber.unsubscribe_token]))

        assert response.status_code == 200
        assert b"Confirm that you want to unsubscribe" in response.content
        subscriber.refresh_from_db()
        assert subscriber.subscribed is True

    def test_confirm_unsubscribe_requires_post(self):
        subscriber = Subscriber.objects.create(email="reader@example.test", subscribed=True)
        client = Client()

        response = client.get(reverse("newsletter:confirm-unsubscribe", args=[subscriber.unsubscribe_token]))

        assert response.status_code == 405
        subscriber.refresh_from_db()
        assert subscriber.subscribed is True

    def test_confirm_unsubscribe_post_unsubscribes_and_redirects(self):
        subscriber = Subscriber.objects.create(email="reader@example.test", subscribed=True)
        client = Client()

        response = client.post(reverse("newsletter:confirm-unsubscribe", args=[subscriber.unsubscribe_token]))

        assert response.status_code == 302
        subscriber.refresh_from_db()
        assert subscriber.subscribed is False

    def test_unsubscribe_post_is_idempotent_and_returns_200_without_redirect(self):
        subscriber = Subscriber.objects.create(email="reader@example.test", subscribed=True)
        client = Client()
        url = reverse("newsletter:unsubscribe", args=[subscriber.unsubscribe_token])

        first = client.post(url)
        second = client.post(url)

        assert first.status_code == 200
        assert second.status_code == 200
        assert "Location" not in first.headers
        assert "Location" not in second.headers
        subscriber.refresh_from_db()
        assert subscriber.subscribed is False
