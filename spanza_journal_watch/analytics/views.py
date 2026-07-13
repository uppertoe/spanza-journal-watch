import logging
from json import JSONDecodeError, loads
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import MultipleObjectsReturned
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.middleware.csrf import get_token
from django.urls import resolve
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from spanza_journal_watch.analytics.middleware import ensure_visitor_id, set_visitor_cookie
from spanza_journal_watch.analytics.models import AnalyticsEvent, NewsletterClick, NewsletterOpen
from spanza_journal_watch.analytics.utils import (
    classify_event_confidence,
    is_probable_automated_event,
    is_probable_automated_newsletter_event,
    set_newsletter_referrer_in_session,
)
from spanza_journal_watch.newsletter.cookies import set_subscribed_cookie
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Hit, Review

logger = logging.getLogger(__name__)


def _get_newsletter(token):
    try:
        newsletter = Newsletter.objects.get(email_token=token)
    except Newsletter.DoesNotExist:
        newsletter = None
        logger.warning("No matching newsletter with token: %s", token)
    return newsletter


def _get_subscriber(email):
    subscriber = Subscriber.first_by_email(email)
    if not subscriber:
        logger.warning("No matching subscriber for email: %s", email)
    return subscriber


def _is_external_url(parsed_url):
    # External URLs should not be resolved before redirection
    return bool(parsed_url.scheme and parsed_url.netloc)


def _get_next_url(request, next):
    parsed_next = urlparse(next)

    # Redirect absolute (external) URLs
    if _is_external_url(parsed_next):
        return HttpResponseRedirect(next)

    try:
        # Catch malformed URLs
        response = HttpResponseRedirect(next)
        view, args, kwargs = resolve(parsed_next[2])
        kwargs["request"] = request
        view(*args, **kwargs)
    except Http404:
        return HttpResponseRedirect("/")
    return response


def track_email_open(request):
    email = request.GET.get("email") or None
    token = request.GET.get("token") or None

    newsletter = _get_newsletter(token)
    subscriber = _get_subscriber(email)

    if newsletter and subscriber:
        automated = is_probable_automated_newsletter_event(request, newsletter)
        tracker = NewsletterOpen(
            subscriber=subscriber,
            newsletter=newsletter,
            user_agent=request.headers.get("user-agent", ""),
            automated=automated,
            human_confidence=classify_event_confidence(automated=automated, subscriber=subscriber),
        )
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk
        request.session["subscriber_email"] = subscriber.email

    response = HttpResponse(_get_tracking_pixel(), content_type="image/png")
    return response


def _get_tracking_pixel():
    """Return cached tracking pixel bytes (loaded once per process)."""
    if not hasattr(_get_tracking_pixel, "_cache"):
        pixel_path = finders.find("images/tracking/pixel.png")
        with open(pixel_path, "rb") as f:
            _get_tracking_pixel._cache = f.read()
    return _get_tracking_pixel._cache


def track_newsletter_link(request, newsletter_token):
    next = request.GET.get("next") or "/"  # is a hardcoded URL
    email = request.GET.get("email") or None

    newsletter = _get_newsletter(newsletter_token)
    subscriber = _get_subscriber(email)

    # This redirect is the first request of a newsletter visit, and the HTML
    # it lands on is CDN-cached (no Set-Cookie), so mint the visitor ID here.
    visitor_id, visitor_cookie_created = ensure_visitor_id(request)

    if newsletter and subscriber:
        automated = is_probable_automated_newsletter_event(request, newsletter)
        tracker = NewsletterClick(
            subscriber=subscriber,
            newsletter=newsletter,
            user_agent=request.headers.get("user-agent", ""),
            automated=automated,
            human_confidence=classify_event_confidence(automated=automated, subscriber=subscriber),
            destination_url=(next or "")[:512],
        )
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk
        request.session["subscriber_email"] = subscriber.email
        set_newsletter_referrer_in_session(request)

        AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
            subscriber_id=subscriber.pk,
            source="newsletter_click",
            metadata={"newsletter_id": newsletter.pk, "destination_url": (next or "")[:512]},
        )

    response = _get_next_url(request, next)
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
    # Sets the session ID on following an email link
    email = request.GET.get("email") or None
    next = request.GET.get("next") or "/"

    visitor_id, visitor_cookie_created = ensure_visitor_id(request)

    subscriber = Subscriber.first_by_email(email)
    if subscriber:
        request.session["subscriber_id"] = subscriber.pk
        request.session["subscriber_email"] = subscriber.email
        set_newsletter_referrer_in_session(request)
        AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            request=request,
            subscriber_id=subscriber.pk,
            source="newsletter_click",
            metadata={"destination_url": (next or "")[:512]},
        )
    else:
        subscriber = None
        logger.warning("No subscriber by this email: %s", email)

    response = _get_next_url(request, next)
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


@csrf_exempt
@require_POST
def track_event(request):
    try:
        payload = loads(request.body.decode("utf-8"))
    except (JSONDecodeError, UnicodeDecodeError):
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

    subscriber_id = request.session.get("subscriber_id")

    # Page context travels in the payload: the beacon request itself is always
    # same-origin, so its Referer/query string say nothing about the page.
    event_metadata = payload.get("metadata") or {}
    page_url = (payload.get("page_url") or "")[:1024]
    utm_params = _extract_utm_params_from_url(page_url)
    if utm_params:
        event_metadata.update(utm_params)

    source = payload.get("source") or ""
    event_path = str(event_metadata.get("path") or urlparse(page_url).path or "")
    skip_record = source == "page_load" and _is_excluded_page_path(event_path)

    visitor_id, visitor_cookie_created = ensure_visitor_id(request)

    if not skip_record:
        AnalyticsEvent.record_event(
            event_type=event_type,
            request=request,
            content_object=review,
            subscriber_id=subscriber_id,
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
