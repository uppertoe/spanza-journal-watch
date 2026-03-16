from urllib.error import URLError
from urllib.request import urlopen

import pytest
from django.core import mail
from django.urls import reverse

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
        assert post_response.status_code == 204

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

    @pytest.mark.mailhog
    def test_mailhog_api_is_reachable(self):
        try:
            with urlopen("http://mailhog:8025/api/v2/messages", timeout=2) as response:
                status = response.status
        except URLError:
            pytest.skip("MailHog not reachable from current test runtime")

        assert status == 200
