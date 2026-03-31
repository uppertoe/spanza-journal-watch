import logging
from json import JSONDecodeError, loads
from urllib.parse import urlparse

from django.contrib.staticfiles import finders
from django.core.exceptions import MultipleObjectsReturned
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.urls import resolve
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from spanza_journal_watch.analytics.models import AnalyticsEvent, NewsletterClick, NewsletterOpen, PageView
from spanza_journal_watch.analytics.utils import (
    is_probable_automated_event,
    is_probable_automated_newsletter_event,
    set_newsletter_referrer_in_session,
)
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
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        subscriber = None
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
        tracker = NewsletterOpen(
            subscriber=subscriber,
            newsletter=newsletter,
            user_agent=request.headers.get("user-agent", ""),
            automated=is_probable_automated_newsletter_event(request, newsletter),
        )
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk

    pixel_path = finders.find("images/tracking/pixel.png")
    with open(pixel_path, "rb") as f:
        response = HttpResponse(f.read(), content_type="image/png")

    return response


def track_newsletter_link(request, newsletter_token):
    next = request.GET.get("next") or "/"  # is a hardcoded URL
    email = request.GET.get("email") or None

    newsletter = _get_newsletter(newsletter_token)
    subscriber = _get_subscriber(email)

    if newsletter and subscriber:
        tracker = NewsletterClick(
            subscriber=subscriber,
            newsletter=newsletter,
            user_agent=request.headers.get("user-agent", ""),
            automated=is_probable_automated_newsletter_event(request, newsletter),
        )
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk
        set_newsletter_referrer_in_session(request)

    return _get_next_url(request, next)


def page_view(request, model=None, slug=None):
    if model == "review":
        try:
            review = Review.objects.get(slug=slug)
            subscriber_id = request.session.get("subscriber_id")
            PageView.record_view(review, subscriber_id, request=request)

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

    try:
        subscriber = Subscriber.objects.get(email=email)
        request.session["subscriber_id"] = subscriber.pk
        set_newsletter_referrer_in_session(request)
    except Subscriber.DoesNotExist:
        subscriber = None
        logger.warning("No subscriber by this email: %s", email)

    return _get_next_url(request, next)


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
    AnalyticsEvent.record_event(
        event_type=event_type,
        request=request,
        content_object=review,
        subscriber_id=subscriber_id,
        source=payload.get("source") or "",
        duration_ms=payload.get("duration_ms"),
        scroll_depth=payload.get("scroll_depth"),
        metadata=payload.get("metadata") or {},
    )
    return JsonResponse({"ok": True})
