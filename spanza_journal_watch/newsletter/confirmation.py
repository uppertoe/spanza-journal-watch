"""Double opt-in: a subscription starts only once the address's owner follows an emailed link.

Nothing about the address is stored in the database until then. The link
carries a random code; the address and where the request came from are held
in the cache under that code until the link is followed or the code expires.
"""

import secrets

from django.conf import settings
from django.core import mail, signing
from django.core.cache import cache, caches
from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.locmem import LocMemCache
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from spanza_journal_watch.utils.functions import get_domain_url

CONFIRM_SALT = "newsletter.subscribe-confirm"
CONFIRM_MAX_AGE = 7 * 24 * 60 * 60  # seven days, as the email says
CONFIRM_CACHE_PREFIX = "newsletter:confirm-code:"
# Set at sign-up when the member asks for the newsletter; acted on once the
# sign-in code has confirmed the address.
SUBSCRIBE_ON_CONFIRM_SESSION_KEY = "subscribe_on_confirm"
# One confirmation email per address in this window, so the form cannot be used
# to fill someone's inbox.
RESEND_INTERVAL = 10 * 60
# Confirmation emails one visitor may set off in an hour, across all addresses.
VISITOR_HOURLY_LIMIT = 5

# Local development runs on DummyCache, which keeps nothing; the codes are then
# held in this process instead, so confirmation links still work there.
_local_code_store = LocMemCache("newsletter-confirm-codes", {"TIMEOUT": CONFIRM_MAX_AGE})


def _code_store():
    store = caches["default"]
    return _local_code_store if isinstance(store, DummyCache) else store


def make_confirm_token(email, source):
    """Store ``(email, source)`` under a new random code and return the code."""
    code = secrets.token_urlsafe(24)
    _code_store().set(f"{CONFIRM_CACHE_PREFIX}{code}", {"e": email, "s": source}, CONFIRM_MAX_AGE)
    return code


def read_confirm_token(token):
    """Return ``(email, source)`` for a known, unexpired code, else None."""
    data = _code_store().get(f"{CONFIRM_CACHE_PREFIX}{token}") if token else None
    if not isinstance(data, dict):
        return None
    email = (data.get("e") or "").strip().lower()
    return (email, data.get("s") or "") if email else None


def discard_confirm_token(token):
    """Forget a code once it has been used, so the link cannot subscribe the address again."""
    _code_store().delete(f"{CONFIRM_CACHE_PREFIX}{token}")


def claim_send_slot(email):
    """True if no confirmation email has gone to this address within RESEND_INTERVAL."""
    return cache.add(
        f"newsletter:confirm-sent:{signing.Signer(salt=CONFIRM_SALT).signature(email)}", 1, RESEND_INTERVAL
    )


def claim_visitor_slot(request):
    """True while this visitor has set off fewer than VISITOR_HOURLY_LIMIT confirmation emails this hour."""
    from allauth.account.adapter import get_adapter

    ip = get_adapter(request).get_client_ip(request)
    key = f"newsletter:confirm-visitor:{signing.Signer(salt=CONFIRM_SALT).signature(ip)}"
    cache.add(key, 0, 60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:
        # The dummy cache used in local development keeps nothing.
        return True
    return count <= VISITOR_HOURLY_LIMIT


def release_send_slot(email):
    """Free the slot again when the confirmation email could not be queued."""
    cache.delete(f"newsletter:confirm-sent:{signing.Signer(salt=CONFIRM_SALT).signature(email)}")


def build_confirmation_request_email(email, code):
    domain = get_domain_url()
    confirm_path = reverse("newsletter:confirm-subscription", args=[code])
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


def subscribe_account_holder(request, user):
    """Subscribe a member whose address has been confirmed, and mark the session as subscribed."""
    from .models import Subscriber

    subscriber = Subscriber.first_by_email(user.email)
    if subscriber is None:
        subscriber = Subscriber.objects.create(
            email=user.email, user=user, subscribed=True, source=Subscriber.Source.USER_SIGNUP
        )
    else:
        update_fields = ["modified"]
        if not subscriber.subscribed:
            subscriber.subscribed = True
            update_fields.append("subscribed")
        if subscriber.user_id is None:
            subscriber.user = user
            update_fields.append("user")
        if len(update_fields) > 1:
            subscriber.save(update_fields=update_fields)
    if request is not None and hasattr(request, "session"):
        request.session["subscribed"] = True
        request.session["subscriber_id"] = subscriber.pk
    return subscriber
