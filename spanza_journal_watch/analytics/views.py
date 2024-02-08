from urllib.parse import urlparse

from django.contrib.staticfiles import finders
from django.core.exceptions import MultipleObjectsReturned
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import resolve

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen, PageView
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Review


def _get_newsletter(token):
    try:
        newsletter = Newsletter.objects.get(email_token=token)
    except Newsletter.DoesNotExist:
        newsletter = None
        print(f"No matching newsletter with token: {token}")
    return newsletter


def _get_subscriber(email):
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        subscriber = None
        print(f"No matching subscriber for email: {email}")
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
        tracker = NewsletterOpen(subscriber=subscriber, newsletter=newsletter)
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
        tracker = NewsletterClick(subscriber=subscriber, newsletter=newsletter)
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk

    return _get_next_url(request, next)


def page_view(request, model=None, slug=None):
    if model == "review":
        try:
            review = Review.objects.get(slug=slug)
            subscriber_id = request.session.get("subscriber_id")
            PageView.record_view(review, subscriber_id)
        except (Review.DoesNotExist, MultipleObjectsReturned) as e:
            print(e)

    return HttpResponse("")


def track_email_click(request):
    # Sets the session ID on following an email link
    email = request.GET.get("email") or None
    next = request.GET.get("next") or "/"

    try:
        subscriber = Subscriber.objects.get(email=email)
        request.session["subscriber_id"] = subscriber.pk
    except Subscriber.DoesNotExist:
        subscriber = None
        print(f"No subscriber by this email: {email}")

    return _get_next_url(request, next)
