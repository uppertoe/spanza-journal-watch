import datetime
import re
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from spanza_journal_watch.utils.functions import get_domain_url

AUTOMATED_USER_AGENT_MARKERS = [
    # Email security scanners
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
    # Generic HTTP clients
    "python-requests",
    "curl/",
    "wget/",
    "go-http-client",
    "java/",
    "axios/",
    "node-fetch",
    "scrapy",
    "httpx/",
    # Headless browsers
    "headlesschrome",
    "phantomjs",
    # SEO / link-analysis crawlers
    "semrushbot",
    "ahrefsbot",
    "mj12bot",
    "dotbot",
    "screaming frog",
    "petalbot",
    "seranking",
    "barkrowler",
    "sogou",
    "cms-checker",
    # AI crawlers
    "claudebot",
    "gptbot",
    "chatgpt-user",
    "anthropic-ai",
    "cohere-ai",
    "bytespider",
    "ccbot",
    # Search engine crawlers
    "googlebot",
    "bingbot",
    "yandexbot",
    "amazonbot",
    "baiduspider",
    "duckduckbot",
    # Uptime / monitoring bots
    "sentryuptimebot",
    "uptimerobot",
    "pingdom",
    "statuscake",
    "site24x7",
    "gatus",
    "uptime-kuma",
    "betteruptime",
    # Headless automation frameworks (JS-executing bots js_verified can't catch)
    "playwright",
    "puppeteer",
    # Additional generic HTTP clients
    "okhttp",
    "aiohttp",
    "bingpreview",
    # Generic self-identifying crawler tokens. Substring match — classification
    # only affects counting/persistence (never blocking), so the rare false
    # positive (e.g. a "Cubot" phone) is an acceptable miscount.
    "crawler",
    "spider",
    "slurp",
    "bot",
    # Social preview bots (these fetch pages to generate link previews, not human reads)
    "facebookexternalhit",
    "twitterbot",
    "linkedinbot",
    "slackbot",
    "discordbot",
    "telegrambot",
    "whatsapp",
    "applebot",
    # Miscellaneous crawlers observed in prod analytics
    "leads-enricher",
    # Google's non-search fetchers (GoogleOther, Google-InspectionTool, ...)
    # carry no "bot" token, so the generic marker above misses them.
    "googleother",
    "google-inspectiontool",
    "google-safety",
    "google-site-verification",
    # Baidu's rendering fetcher: a 2016 Nexus 5 emulator UA ending in "T7/x.y".
    "t7/",
]

NEWSLETTER_AUTOMATION_WINDOW = timedelta(seconds=60)

# Event types recorded without JS that modern browsers would always surface with
# sec-fetch-* headers. Missing those headers on these events strongly implies a
# non-browser client that still got through the UA markers.
_SEC_FETCH_STRICT_EVENT_TYPES = frozenset({"search"})

# Chromium has sent the low-entropy client hints (sec-ch-ua, sec-ch-ua-mobile,
# sec-ch-ua-platform) on every request, beacons included, since version 89.
CLIENT_HINTS_MIN_CHROMIUM_MAJOR = 89

_CHROMIUM_MAJOR_RE = re.compile(r"(?<![a-z])chrome/(\d+)")
_SEC_CH_UA_VERSION_RE = re.compile(r'v="(\d+)')
_IOS_MAJOR_RE = re.compile(r"(?:iphone|cpu) os (\d+)_")

# Platform token in sec-ch-ua-platform -> the substring a matching UA string carries.
_CLIENT_HINT_PLATFORM_MARKERS = {
    "windows": ("windows nt",),
    "macos": ("macintosh",),
    "android": ("android",),
    "chrome os": ("cros",),
    "chromium os": ("cros",),
    "linux": ("linux",),
}

# Chrome 100 shipped on 2022-03-29 and a new major has followed every four weeks
# since. The estimate runs a little ahead of reality, which only makes the
# stale-browser check more lenient.
_CHROMIUM_ANCHOR_MAJOR = 100
_CHROMIUM_ANCHOR_DATE = datetime.date(2022, 3, 29)
_CHROMIUM_RELEASE_CADENCE_DAYS = 28
# Majors behind the estimated current release before a Chromium UA counts as
# stale. Thirty majors is roughly two and a half years; real installs that old
# are rare, and interactors are protected by the sweeper regardless.
STALE_CHROMIUM_MAJORS_BEHIND = 30
# iOS releases before this are no longer able to run a current Safari or Chrome.
STALE_IOS_MAJOR_BEFORE = 15


def chromium_major(user_agent):
    """Return the Chrome/NNN major from a lower-cased UA, or None."""
    match = _CHROMIUM_MAJOR_RE.search(user_agent)
    return int(match.group(1)) if match else None


def client_hints_anomaly(request):
    """Return a reason token when a Chromium UA and its client hints disagree.

    Every Chromium build since 89 sends sec-ch-ua and sec-ch-ua-platform with
    every request. A UA string claiming such a build without the headers, or
    with a hinted major or platform that contradicts the string, was typed in
    by a script rather than produced by a browser. Firefox and Safari (CriOS
    included) never send the hints and never claim "Chrome/", so they are out
    of scope.
    """
    user_agent = (request.headers.get("user-agent") or "").lower()
    major = chromium_major(user_agent)
    if major is None or major < CLIENT_HINTS_MIN_CHROMIUM_MAJOR:
        return None

    sec_ch_ua = request.headers.get("sec-ch-ua") or ""
    if not sec_ch_ua:
        return "client_hints_missing"
    hinted_majors = {int(v) for v in _SEC_CH_UA_VERSION_RE.findall(sec_ch_ua)}
    if hinted_majors and major not in hinted_majors:
        return "client_hints_mismatch"

    platform = (request.headers.get("sec-ch-ua-platform") or "").strip('"').lower()
    markers = _CLIENT_HINT_PLATFORM_MARKERS.get(platform)
    if markers and not any(marker in user_agent for marker in markers):
        return "client_hints_mismatch"
    return None


def client_hints_enforced():
    return bool(getattr(settings, "ANALYTICS_ENFORCE_CLIENT_HINTS", False))


def expected_chromium_major(today=None):
    today = today or timezone.localdate()
    elapsed = (today - _CHROMIUM_ANCHOR_DATE).days
    return _CHROMIUM_ANCHOR_MAJOR + max(0, elapsed) // _CHROMIUM_RELEASE_CADENCE_DAYS


def stale_browser_reason(user_agent, today=None):
    """Return a token when a UA claims a browser too old to be in real use."""
    user_agent = (user_agent or "").lower()
    major = chromium_major(user_agent)
    if major is not None and major < expected_chromium_major(today) - STALE_CHROMIUM_MAJORS_BEHIND:
        return "stale_chromium"
    ios = _IOS_MAJOR_RE.search(user_agent)
    if ios and int(ios.group(1)) < STALE_IOS_MAJOR_BEFORE:
        return "stale_ios"
    return None


def _automated_ua_markers():
    """Default UA markers plus any added via settings (no code deploy needed)."""
    extra = getattr(settings, "ANALYTICS_EXTRA_AUTOMATED_UA_MARKERS", ())
    return AUTOMATED_USER_AGENT_MARKERS + [m.lower() for m in extra]


def classify_automated_reason(request, event_type=None):
    """Return a coarse reason token if the request looks automated, else None.

    The token is the deciding signal (for observability / AutomatedRequestCount),
    not a per-marker value — kept low-cardinality on purpose.
    """
    user_agent = (request.headers.get("user-agent") or "").lower()
    if not user_agent:
        return "empty_ua"
    if any(marker in user_agent for marker in _automated_ua_markers()):
        return "ua_marker"
    # Generic crawler convention: UA contains a URL identifying the bot
    # (e.g. "... +http://example.com/bot"). Real browsers never do this.
    if "+http://" in user_agent or "+https://" in user_agent:
        return "bot_url"
    # Research-bot self-identification ("+contact: ..." in UA body).
    if "+contact:" in user_agent:
        return "bot_contact"
    # Real Chrome UAs always carry AppleWebKit/ and Safari/ tokens. Bots often
    # fabricate a "Chrome/NNN.0.0.0" stub — catch the mismatch.
    if "chrome/" in user_agent and "applewebkit/" not in user_agent:
        return "chrome_fabrication"
    if client_hints_enforced():
        anomaly = client_hints_anomaly(request)
        if anomaly is not None:
            return anomaly

    # Common prefetch/scanner headers
    if (request.headers.get("purpose") or "").lower() in {"prefetch", "preview"}:
        return "prefetch"
    if (request.headers.get("x-purpose") or "").lower() in {"preview"}:
        return "preview"
    if (request.headers.get("x-moz") or "").lower() == "prefetch":
        return "moz_prefetch"

    sec_fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if sec_fetch_mode == "no-cors" and sec_fetch_site == "cross-site":
        # Often image preloading/proxy behavior
        return "image_proxy"

    # For events that only fire from interactive page loads (e.g. SEARCH), a
    # modern browser always sends sec-fetch-* headers. Their absence points to
    # a scripted client whose UA doesn't match our marker list.
    if event_type in _SEC_FETCH_STRICT_EVENT_TYPES and not sec_fetch_mode:
        return "sec_fetch_strict"

    return None


def is_probable_automated_event(request, event_type=None):
    return classify_automated_reason(request, event_type=event_type) is not None


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


_SEARCH_DOMAINS = frozenset(
    [
        "google.com",
        "google.co.uk",
        "google.com.au",
        "google.co.nz",
        "bing.com",
        "duckduckgo.com",
        "yahoo.com",
        "ecosia.org",
        "brave.com",
        "startpage.com",
        "search.yahoo.com",
    ]
)

_SOCIAL_DOMAINS = frozenset(
    [
        "twitter.com",
        "x.com",
        "t.co",
        "facebook.com",
        "fb.com",
        "linkedin.com",
        "bsky.app",
        "bluesky.social",
        "instagram.com",
        "reddit.com",
        "old.reddit.com",
        "mastodon.social",
    ]
)

_REFERRER_SESSION_KEY = "analytics_referrer"

REFERRER_NEWSLETTER = "newsletter"
REFERRER_SEARCH = "search"
REFERRER_SOCIAL = "social"
REFERRER_DIRECT = "direct"
REFERRER_INTERNAL = "internal"
REFERRER_OTHER = "other"


def _get_own_domain():
    return (getattr(settings, "ALLOWED_HOSTS", None) or [None])[0] or ""


def _categorize_from_header(referer, own_domain=""):
    if not referer:
        return REFERRER_DIRECT
    try:
        parsed = urlparse(referer)
        host = (parsed.netloc or "").lower().lstrip("www.")
    except Exception:
        return REFERRER_OTHER
    if not host:
        return REFERRER_DIRECT
    if own_domain and host.endswith(own_domain.lstrip("www.")):
        return REFERRER_INTERNAL
    if any(host == d or host.endswith("." + d) for d in _SEARCH_DOMAINS):
        return REFERRER_SEARCH
    if any(host == d or host.endswith("." + d) for d in _SOCIAL_DOMAINS):
        return REFERRER_SOCIAL
    return REFERRER_OTHER


_UTM_SOCIAL_SOURCES = frozenset(["twitter", "x", "facebook", "linkedin", "bluesky", "instagram", "reddit", "mastodon"])


def _categorize_from_utm_source(utm_source):
    """Return a referrer category if utm_source is non-empty, else None."""
    utm_source = (utm_source or "").strip().lower()
    if not utm_source:
        return None
    if "newsletter" in utm_source or "email" in utm_source:
        return REFERRER_NEWSLETTER
    if utm_source in _UTM_SOCIAL_SOURCES:
        return REFERRER_SOCIAL
    if "search" in utm_source or "google" in utm_source or "bing" in utm_source:
        return REFERRER_SEARCH
    return REFERRER_OTHER


def categorize_referrer(request, referrer=None, utm_source=None):
    """
    Return the referrer category for the current request.

    Priority:
    1. Session override (set when a subscriber follows a newsletter link).
    2. UTM parameters (explicit utm_source, else utm_source in query string).
    3. Referrer URL (explicit, else HTTP Referer header).
    Session entries expire at local midnight on the day they were set.

    The explicit referrer/utm_source arguments carry the *page* context from
    the JS beacon payload — the beacon request itself is always same-origin,
    so its own header and query string say nothing about the traffic source.
    """
    session_entry = (request.session.get(_REFERRER_SESSION_KEY) or {}) if hasattr(request, "session") else {}
    if session_entry:
        try:
            expires = datetime.datetime.fromisoformat(session_entry["expires"])
            if timezone.now() < expires:
                return session_entry["category"]
        except (KeyError, ValueError):
            pass

    utm_category = _categorize_from_utm_source(utm_source if utm_source is not None else request.GET.get("utm_source"))
    if utm_category is not None:
        return utm_category

    if referrer is None:
        referrer = request.headers.get("referer") or request.headers.get("referrer") or ""
    own_domain = _get_own_domain()
    return _categorize_from_header(referrer, own_domain)


def extract_referrer_domain(request, referrer=None):
    """Return the bare domain from the referrer URL or HTTP Referer header."""
    referer = (
        referrer if referrer is not None else request.headers.get("referer") or request.headers.get("referrer") or ""
    )
    if not referer:
        return ""
    try:
        parsed = urlparse(referer)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host[:255]
    except Exception:
        return ""


def set_newsletter_referrer_in_session(request):
    """
    Tag the session so that analytics events from this visitor during the
    rest of today are attributed to the newsletter.
    """
    if not hasattr(request, "session"):
        return
    now_local = timezone.localtime()
    tomorrow = now_local.date() + datetime.timedelta(days=1)
    end_of_day = timezone.make_aware(
        datetime.datetime.combine(tomorrow, datetime.time.min),
        timezone.get_current_timezone(),
    )
    request.session[_REFERRER_SESSION_KEY] = {
        "category": REFERRER_NEWSLETTER,
        "expires": end_of_day.isoformat(),
    }
