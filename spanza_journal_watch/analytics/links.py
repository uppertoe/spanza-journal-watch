"""Signed, subscriber-scoped links for outgoing email.

Every link in a newsletter or confirmation email passes through the analytics
redirector so the click can be attributed to the subscriber. The destination
is signed together with the subscriber's opaque tracking token, so the
redirector only sends readers to destinations Journal Watch itself placed in
an email, and a click can only be attributed to the subscriber that email was
addressed to. Subscriber email addresses never appear in the URL.
"""

from urllib.parse import urlencode, urlparse

from django.core import signing
from django.urls import reverse
from django.utils.crypto import constant_time_compare

from spanza_journal_watch.utils.functions import get_domain_url

SIGNING_SALT = "analytics.email-link"


def sign_destination(tracking_token, destination):
    return signing.Signer(salt=SIGNING_SALT).signature(f"{tracking_token}\n{destination}")


def destination_is_signed(tracking_token, destination, signature):
    if not (tracking_token and destination and signature):
        return False
    return constant_time_compare(sign_destination(tracking_token, destination), signature)


def has_web_scheme(url):
    parsed = urlparse(url)
    if not parsed.scheme:
        return url.startswith("/") and not url.startswith("//")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class EmailLinkBuilder:
    """Builds the tracked links for one email to one subscriber.

    ``newsletter_token`` ties the links to a particular newsletter send;
    without it the links go through the plain click redirector used by
    confirmation emails.
    """

    def __init__(self, subscriber, newsletter_token=None, domain=None):
        self.subscriber = subscriber
        self.tracking_token = subscriber.ensure_tracking_token()
        self.newsletter_token = newsletter_token
        self.domain = domain if domain is not None else get_domain_url()

    def _redirector_path(self):
        if self.newsletter_token:
            return reverse("analytics:track_newsletter_email_link", args=[self.newsletter_token])
        return reverse("analytics:track_email_click")

    def url(self, destination):
        destination = str(destination or "/")
        query = urlencode(
            {
                "t": self.tracking_token,
                "u": destination,
                "s": sign_destination(self.tracking_token, destination),
            }
        )
        return f"{self.domain}{self._redirector_path()}?{query}"

    def pixel_url(self):
        query = urlencode({"t": self.tracking_token, "token": self.newsletter_token or ""})
        return f"{self.domain}{reverse('analytics:track_email_open')}?{query}"
