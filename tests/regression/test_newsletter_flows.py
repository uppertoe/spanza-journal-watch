import re

import pytest
from django.core import mail
from django.urls import reverse

from spanza_journal_watch.newsletter.confirmation import build_confirmation_request_email, make_confirm_token
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber


@pytest.mark.django_db
class TestNewsletterFlows:
    def test_subscribe_flow_htmx(self, route_client, regression_baseline):
        response = route_client.post(
            reverse("newsletter:subscribe"),
            data={"email": "new-subscriber@example.test"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 302
        assert reverse("newsletter:success") in response.headers.get("Location", "")
        # Double opt-in: nothing is stored until the emailed link is followed
        assert not Subscriber.objects.filter(email="new-subscriber@example.test").exists()

        code = make_confirm_token("new-subscriber@example.test", Subscriber.Source.SUBSCRIBE_FORM)
        request_email = build_confirmation_request_email("new-subscriber@example.test", code)
        assert request_email.subject == "Confirm your Journal Watch subscription"
        assert "Confirm subscription" in request_email.alternatives[0][0]
        confirm_path = re.search(r"https?://[^/\s]+(/newsletter/confirm/\S+)", request_email.body).group(1)

        # Opening the link (as a mail scanner would) confirms nothing
        assert route_client.get(confirm_path).status_code == 200
        assert not Subscriber.objects.filter(email="new-subscriber@example.test").exists()

        assert code in confirm_path
        assert "new-subscriber" not in confirm_path

        confirm_response = route_client.post(confirm_path)
        assert confirm_response.status_code == 200
        assert "base.html" in [t.name for t in confirm_response.templates]
        assert "Your subscription to the Journal Watch newsletter is confirmed." in confirm_response.content.decode()
        assert Subscriber.objects.filter(email="new-subscriber@example.test", subscribed=True).exists()

    def test_unsubscribe_get_and_post(self, route_client, regression_baseline):
        subscriber = Subscriber.objects.filter(subscribed=True).order_by("pk").first()
        assert subscriber is not None

        get_response = route_client.get(
            reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": subscriber.unsubscribe_token})
        )
        assert get_response.status_code == 200
        assert "newsletter/unsubscribe.html" in [t.name for t in get_response.templates if t.name]

        post_response = route_client.post(
            reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": subscriber.unsubscribe_token})
        )
        assert post_response.status_code == 200
        assert "Location" not in post_response

        subscriber.refresh_from_db()
        assert subscriber.subscribed is False

    def test_unsubscribe_invalid_token(self, route_client, regression_baseline):
        response = route_client.get(reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": "missing-token"}))
        assert response.status_code == 302
        assert response.headers.get("Location", "").endswith(reverse("home"))

    def test_confirmation_email_content(self, regression_baseline):
        subscriber = Subscriber.objects.order_by("pk").first()
        assert subscriber is not None

        email = subscriber.generate_confirmation_email()

        assert email.subject == "Journal Watch Subscription"
        assert subscriber.email in email.to
        assert "List-Unsubscribe" in email.extra_headers
        assert "List-Unsubscribe-Post" in email.extra_headers
        assert len(email.alternatives) == 1

        body_html = email.alternatives[0][0]
        assert "unsubscribe" in body_html.lower()
        assert "journal watch" in body_html.lower()

    def test_newsletter_generate_emails(self, regression_baseline):
        newsletter = Newsletter.objects.order_by("pk").first()
        subscriber = Subscriber.objects.order_by("pk").first()

        if not newsletter or not subscriber:
            pytest.skip("Newsletter email generation checks require at least one newsletter and subscriber")

        messages = newsletter.generate_emails([subscriber])

        assert len(messages) == 1
        message = messages[0]
        assert newsletter.subject == message.subject
        assert subscriber.email in message.to
        assert "List-Unsubscribe" in message.extra_headers
        assert message.alternatives

    def test_locmem_backend_still_captures_mail(self, settings, regression_baseline):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

        subscriber = Subscriber.objects.order_by("pk").first()
        assert subscriber is not None

        email = subscriber.generate_confirmation_email()
        email.send()

        assert len(mail.outbox) >= 1
        assert mail.outbox[-1].subject == "Journal Watch Subscription"
