import logging
from json import JSONDecodeError, loads
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import MultipleObjectsReturned
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.middleware.csrf import get_token
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from spanza_journal_watch.analytics.links import destination_is_signed, has_web_scheme
from spanza_journal_watch.analytics.middleware import ensure_visitor_id, set_visitor_cookie
from spanza_journal_watch.analytics.models import (
    NEWSLETTER_CLICK_SOURCE,
    AnalyticsEvent,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.analytics.utils import (
    classify_event_confidence,
    classify_newsletter_event_reason,
    is_probable_automated_event,
    set_newsletter_referrer_in_session,
)
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.cookies import set_subscribed_cookie
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Hit, Review
from spanza_journal_watch.utils.functions import get_domain_url

logger = logging.getLogger(__name__)

# Hosts that newsletters sent before destinations were signed linked to
# directly. Unsigned links to anywhere else are refused.
_LEGACY_EXTERNAL_HOSTS = frozenset({"spanza.org.au", "www.spanza.org.au"})

# Source of the page-visit event for a click on a link in a confirmation or
# other non-newsletter email.
EMAIL_CLICK_SOURCE = "email_click"


def _get_newsletter(token):
    try:
        newsletter = Newsletter.objects.get(email_token=token)
    except Newsletter.DoesNotExist:
        newsletter = None
        logger.warning("No matching newsletter with token: %s", token)
    return newsletter


def _get_subscriber(request):
    """Identify the subscriber from the tracking token in the link.

    Newsletters sent before tracking tokens existed carry the address instead.
    Those links still lead to their destination but identify no one: an
    address in a URL would let anyone check whether it is subscribed.
    """
    tracking_token = request.GET.get("t") or ""
    if tracking_token:
        return Subscriber.objects.filter(tracking_token=tracking_token).first()
    return None


def _is_local_path(request, url):
    if not url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return False
    try:
        resolve(urlparse(url).path)
    except Resolver404:
        return False
    return True


def _is_legacy_external_destination(url):
    """Unsigned external links are followed only where an older newsletter could have contained them."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    if parsed.hostname in _LEGACY_EXTERNAL_HOSTS:
        return True
    return PubmedArticle.objects.filter(article_url=url).exists()


def _resolve_email_link(request):
    """Return ``(subscriber, destination)`` for an email tracking link.

    Links built by ``EmailLinkBuilder`` carry the destination as ``u`` with a
    signature over the subscriber's tracking token and the destination. A link
    whose signature does not verify is not trusted at all: the reader lands on
    the home page and nothing is attributed. Older links carry an unsigned
    ``next``; those are followed only to this site or to a small set of
    external destinations an earlier newsletter could have contained.
    """
    subscriber = _get_subscriber(request)

    signed = request.GET.get("u")
    if signed is not None:
        tracking_token = subscriber.tracking_token if subscriber else ""
        if has_web_scheme(signed) and destination_is_signed(tracking_token, signed, request.GET.get("s", "")):
            return subscriber, signed
        logger.warning("Refused email link with a missing or invalid signature")
        return None, "/"

    legacy = request.GET.get("next") or "/"
    if _is_local_path(request, legacy) or _is_legacy_external_destination(legacy):
        return subscriber, legacy
    logger.warning("Refused unsigned email link to %s", legacy[:200])
    return subscriber, "/"


def _identify_subscriber_in_session(request, subscriber):
    """Identify the subscriber in the session, unless it already holds another.

    A forwarded newsletter carries the original recipient's links; the person
    who follows them keeps the identity their session already has.
    """
    existing = request.session.get("subscriber_id")
    if existing and existing != subscriber.pk:
        return
    request.session["subscriber_id"] = subscriber.pk
    request.session["subscriber_email"] = subscriber.email


def _local_destination_path(request, url):
    """Return the path of ``url`` if it is on this site, else None."""
    hosts = {request.get_host()}
    site_host = urlparse(get_domain_url()).netloc
    if site_host:
        hosts.add(site_host)
    if not url_has_allowed_host_and_scheme(url, allowed_hosts=hosts):
        return None
    return urlparse(url).path or "/"


def track_email_open(request):
    # Link checkers send HEAD before (or instead of) fetching; only a GET is
    # an image load.
    if request.method == "GET":
        newsletter = _get_newsletter(request.GET.get("token") or None)
        subscriber = _get_subscriber(request)

        if newsletter and subscriber:
            reason = classify_newsletter_event_reason(request, newsletter, subscriber, open_pixel=True)
            automated = reason is not None
            NewsletterOpen.objects.create(
                subscriber=subscriber,
                newsletter=newsletter,
                user_agent=request.headers.get("user-agent", ""),
                automated=automated,
                automated_reason=reason or "",
                human_confidence=classify_event_confidence(automated=automated, subscriber=subscriber),
            )

    return HttpResponse(_get_tracking_pixel(), content_type="image/png")


def _get_tracking_pixel():
    """Return cached tracking pixel bytes (loaded once per process)."""
    if not hasattr(_get_tracking_pixel, "_cache"):
        pixel_path = finders.find("images/tracking/pixel.png")
        with open(pixel_path, "rb") as f:
            _get_tracking_pixel._cache = f.read()
    return _get_tracking_pixel._cache


def track_newsletter_link(request, newsletter_token):
    subscriber, destination = _resolve_email_link(request)
    response = HttpResponseRedirect(destination)
    # Link checkers send HEAD before the GET; only the GET is recorded.
    if request.method != "GET":
        return response

    newsletter = _get_newsletter(newsletter_token)

    # This redirect is the first request of a newsletter visit, and the HTML
    # it lands on is CDN-cached (no Set-Cookie), so mint the visitor ID here.
    visitor_id, visitor_cookie_created = ensure_visitor_id(request)

    if newsletter and subscriber:
        reason = classify_newsletter_event_reason(request, newsletter, subscriber)
        automated = reason is not None
        click = NewsletterClick.objects.create(
            subscriber=subscriber,
            newsletter=newsletter,
            user_agent=request.headers.get("user-agent", ""),
            automated=automated,
            automated_reason=reason or "",
            human_confidence=classify_event_confidence(automated=automated, subscriber=subscriber),
            destination_url=destination[:512],
        )
        click.check_scanner_burst()

        _identify_subscriber_in_session(request, subscriber)
        set_newsletter_referrer_in_session(request)

        # A link to another site is not a visit to this one; the click row
        # above is the whole record of it.
        landing_page = _local_destination_path(request, destination)
        if landing_page is not None:
            AnalyticsEvent.record_event(
                event_type=AnalyticsEvent.EventType.PAGE_VISIT,
                request=request,
                subscriber_id=subscriber.pk,
                source=NEWSLETTER_CLICK_SOURCE,
                metadata={
                    "newsletter_id": newsletter.pk,
                    "newsletter_click_id": click.pk,
                    "destination_url": destination[:512],
                },
                landing_page=landing_page,
                automated=click.automated,
                automated_reason=click.automated_reason,
            )

    if subscriber:
        set_subscribed_cookie(response)
    if visitor_cookie_created:
        set_visitor_cookie(response, visitor_id)
    return response


def page_view(request, model=None, slug=None):
    if model == "review":
        try:
            review = Review.objects.get(slug=slug)
            # Keep human-facing hit count resilient to scanners and duplicate viewport triggers
            if not is_probable_automated_event(request):
                viewed_key = "model_review_viewed"
                viewed_objects = request.session.get(viewed_key, [])
                if review.id not in viewed_objects:
                    Hit.update_page_count(review)
                    viewed_objects.append(review.id)
                    request.session[viewed_key] = viewed_objects
        except (Review.DoesNotExist, MultipleObjectsReturned) as e:
            logger.warning("Error tracking review pageview: %s", e)

    return HttpResponse("")


def track_email_click(request):
    # Links in confirmation and other one-off emails. Sets the session ID on
    # following the link; the visit is not attributed to the newsletter.
    subscriber, destination = _resolve_email_link(request)
    response = HttpResponseRedirect(destination)
    if request.method != "GET":
        return response

    visitor_id, visitor_cookie_created = ensure_visitor_id(request)

    if subscriber:
        _identify_subscriber_in_session(request, subscriber)
        landing_page = _local_destination_path(request, destination)
        if landing_page is not None:
            AnalyticsEvent.record_event(
                event_type=AnalyticsEvent.EventType.PAGE_VISIT,
                request=request,
                subscriber_id=subscriber.pk,
                source=EMAIL_CLICK_SOURCE,
                metadata={"destination_url": destination[:512]},
                landing_page=landing_page,
            )

    if subscriber:
        set_subscribed_cookie(response)
    if visitor_cookie_created:
        set_visitor_cookie(response, visitor_id)
    return response


# Staff/utility paths whose page loads should not be recorded — mirrors the
# exclusions the old server-side PageVisitAnalyticsMiddleware applied.
_PAGE_VISIT_EXCLUDED_PREFIXES = ("/editorial/", "/o/", "/__debug__/", "/tinymce/", "/markdownx/")


def _is_excluded_page_path(path):
    admin_url = (getattr(settings, "ADMIN_URL", "admin/") or "").strip("/")
    admin_prefix = f"/{admin_url}/" if admin_url else None
    if admin_prefix and path.startswith(admin_prefix):
        return True
    return any(path.startswith(prefix) for prefix in _PAGE_VISIT_EXCLUDED_PREFIXES)


def _extract_utm_params_from_url(page_url):
    """Return utm_source/medium/campaign parsed from the page URL's query string."""
    try:
        query = parse_qs(urlparse(page_url or "").query)
    except ValueError:
        return {}
    params = {}
    for key in ("utm_source", "utm_medium", "utm_campaign"):
        value = (query.get(key) or [""])[0].strip()
        if value:
            params[key] = value[:128]
    return params


# A beacon carries a handful of short fields; anything larger is not ours.
MAX_BEACON_BYTES = 8 * 1024
MAX_METADATA_KEYS = 20
MAX_METADATA_VALUE_LENGTH = 512


def _bounded_scalar(value):
    if isinstance(value, str):
        return value[:MAX_METADATA_VALUE_LENGTH]
    if isinstance(value, bool | int | float) or value is None:
        return value
    raise ValueError


def _bounded_metadata(metadata):
    """Keep the beacon's metadata to a few short values or short lists of them."""
    if not isinstance(metadata, dict):
        return {}
    bounded = {}
    for key, value in list(metadata.items())[:MAX_METADATA_KEYS]:
        try:
            if isinstance(value, list):
                bounded[str(key)[:64]] = [_bounded_scalar(item) for item in value[:MAX_METADATA_KEYS]]
            else:
                bounded[str(key)[:64]] = _bounded_scalar(value)
        except ValueError:
            continue
    return bounded


@csrf_exempt
@require_POST
def track_event(request):
    if len(request.body) > MAX_BEACON_BYTES:
        return HttpResponseBadRequest("Analytics payload too large")
    try:
        payload = loads(request.body.decode("utf-8"))
    except (JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid analytics payload")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("Invalid analytics payload")

    event_type = (payload.get("event_type") or "").strip()
    allowed_event_types = {choice for choice, _label in AnalyticsEvent.EventType.choices}
    if event_type not in allowed_event_types:
        return HttpResponseBadRequest("Unsupported analytics event type")

    review_id = payload.get("review_id")
    review = None
    if review_id is not None:
        try:
            review = Review.objects.select_related("article__journal", "author").get(pk=int(review_id))
        except (Review.DoesNotExist, TypeError, ValueError):
            return HttpResponseBadRequest("Invalid review")

    # Page context travels in the payload: the beacon request itself is always
    # same-origin, so its Referer/query string say nothing about the page.
    event_metadata = _bounded_metadata(payload.get("metadata"))
    page_url = (payload.get("page_url") or "")[:1024]
    utm_params = _extract_utm_params_from_url(page_url)
    if utm_params:
        event_metadata.update(utm_params)

    source = payload.get("source") or ""
    event_path = str(event_metadata.get("path") or urlparse(page_url).path or "")
    skip_record = source == "page_load" and _is_excluded_page_path(event_path)
    # Staff browsing the public site are not readers.
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        skip_record = True

    visitor_id, visitor_cookie_created = ensure_visitor_id(request, client_visitor_id=payload.get("visitor_id"))

    if not skip_record:
        AnalyticsEvent.record_event(
            event_type=event_type,
            request=request,
            content_object=review,
            # Reading is recorded against the anonymous visitor only, never the
            # subscriber the session may have identified from a newsletter link.
            subscriber_id=None,
            source=source,
            duration_ms=payload.get("duration_ms"),
            scroll_depth=payload.get("scroll_depth"),
            metadata=event_metadata,
            js_verified=True,
            referrer=(payload.get("referrer") or "")[:1024],
            utm_source=utm_params.get("utm_source", ""),
            landing_page=payload.get("landing_page") or "",
            share_token=payload.get("share_token") or "",
        )

    # First-time visitors arrive via CDN-cached HTML that never sets cookies,
    # so the beacon response is where both the visitor ID and the CSRF cookie
    # (needed for the subscribe form's X-CSRFToken header) get minted.
    get_token(request)
    # Prevent the response from setting or deleting the session cookie.  This
    # endpoint is called via sendBeacon during visibilitychange / pagehide.  If
    # the response includes Set-Cookie it can overwrite the authenticated
    # session cookie due to a race with the concurrent navigation request —
    # especially after login, where cycle_key() has already deleted the old
    # session.
    #
    # modified = False blocks SessionMiddleware's SET branch, but not the
    # DELETE branch (Django #11506).  _no_session_cookie tells our
    # SafeSessionCookieMiddleware to strip the cookie from the response
    # regardless of which branch fired.
    request.session.modified = False
    request._no_session_cookie = True
    response = JsonResponse({"ok": True})
    if visitor_cookie_created:
        set_visitor_cookie(response, visitor_id)
    return response
