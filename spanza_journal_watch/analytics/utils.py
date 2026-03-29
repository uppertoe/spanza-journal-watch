from datetime import timedelta

from django.template.loader import render_to_string
from django.utils import timezone

from spanza_journal_watch.utils.functions import get_domain_url

AUTOMATED_USER_AGENT_MARKERS = [
    "googleimageproxy",
    "google-read-aloud",
    "proofpoint",
    "urlscan",
    "barracuda",
    "mimecast",
    "safelinks",
    "symantec",
    "trend micro",
    "talos",
    "cloudflare",
    "python-requests",
    "curl/",
    "wget/",
]

NEWSLETTER_AUTOMATION_WINDOW = timedelta(seconds=5)


def is_probable_automated_event(request):
    user_agent = (request.headers.get("user-agent") or "").lower()
    if any(marker in user_agent for marker in AUTOMATED_USER_AGENT_MARKERS):
        return True

    # Common prefetch/scanner headers
    if (request.headers.get("purpose") or "").lower() in {"prefetch", "preview"}:
        return True
    if (request.headers.get("x-purpose") or "").lower() in {"preview"}:
        return True
    if (request.headers.get("x-moz") or "").lower() == "prefetch":
        return True

    sec_fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if sec_fetch_mode == "no-cors" and sec_fetch_site == "cross-site":
        # Often image preloading/proxy behavior
        return True

    return False


def is_probable_automated_newsletter_event(request, newsletter):
    if is_probable_automated_event(request):
        return True

    if newsletter and newsletter.send_date:
        if timezone.now() - newsletter.send_date <= NEWSLETTER_AUTOMATION_WINDOW:
            return True

    return False


def classify_event_confidence(*, automated, subscriber=None):
    if automated:
        return "suspected_automated"
    if subscriber is not None:
        return "known_subscriber_human"
    return "probable_human"


def click_tracker(email):
    context = {"email": email, "domain": get_domain_url()}
    template = "analytics/click_tracker.txt"
    return render_to_string(template, context)
