"""Double opt-in: a subscription starts only once the address's owner follows a signed link.

Nothing about the address is stored until then; the link itself carries the
address and where the request came from.
"""

from django.conf import settings
from django.core import mail, signing
from django.core.cache import cache
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from spanza_journal_watch.utils.functions import get_domain_url

CONFIRM_SALT = "newsletter.subscribe-confirm"
CONFIRM_MAX_AGE = 7 * 24 * 60 * 60  # seven days, as the email says
# One confirmation email per address in this window, so the form cannot be used
# to fill someone's inbox.
RESEND_INTERVAL = 10 * 60


def make_confirm_token(email, source):
    return signing.dumps({"e": email, "s": source}, salt=CONFIRM_SALT)


def read_confirm_token(token):
    """Return ``(email, source)`` from a valid, unexpired link, else None."""
    try:
        data = signing.loads(token, salt=CONFIRM_SALT, max_age=CONFIRM_MAX_AGE)
    except signing.BadSignature:
        return None
    email = (data.get("e") or "").strip().lower()
    return (email, data.get("s") or "") if email else None


def claim_send_slot(email):
    """True if no confirmation email has gone to this address within RESEND_INTERVAL."""
    return cache.add(
        f"newsletter:confirm-sent:{signing.Signer(salt=CONFIRM_SALT).signature(email)}", 1, RESEND_INTERVAL
    )


def build_confirmation_request_email(email, source):
    domain = get_domain_url()
    confirm_path = reverse("newsletter:confirm-subscription", args=[make_confirm_token(email, source)])
    context = {
        "email": email,
        "confirm_url": f"{domain}{confirm_path}",
        "spanza_logo_url": f"{domain}{static('images/logo/spanza-logo-blue.png')}",
    }
    message = mail.EmailMultiAlternatives(
        subject="Confirm your Journal Watch subscription",
        body=render_to_string("newsletter/email_subscribe_request.txt", context),
        from_email=settings.SUBSCRIBE_FROM_EMAIL,
        to=[email],
        reply_to=[settings.NEWSLETTER_REPLY_TO],
    )
    message.attach_alternative(render_to_string("newsletter/email_subscribe_request.html", context), "text/html")
    message.metadata = {"type": "subscription_request"}
    message.tags = ["subscription-request"]
    return message
