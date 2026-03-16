from django.template.loader import render_to_string

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
]


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


def click_tracker(email):
    context = {"email": email, "domain": get_domain_url()}
    template = "analytics/click_tracker.txt"
    return render_to_string(template, context)
