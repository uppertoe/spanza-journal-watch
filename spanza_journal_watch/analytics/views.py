from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.shortcuts import redirect

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber


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


def track_email_open(request):
    email = request.GET.get("email")
    token = request.GET.get("token")

    newsletter = _get_newsletter(token)
    subscriber = _get_subscriber(email)

    if newsletter and subscriber:
        tracker = NewsletterOpen(email_address=subscriber.email, newsletter=newsletter)
        tracker.save()

    pixel_path = finders.find("images/tracking/pixel.png")
    with open(pixel_path, "rb") as f:
        response = HttpResponse(f.read(), content_type="image/png")

    return response


def track_email_link(request, newsletter_token):
    next = request.GET.get("next")
    email = request.GET.get("email")

    newsletter = _get_newsletter(newsletter_token)
    subscriber = _get_subscriber(email)

    if newsletter and subscriber:
        tracker = NewsletterClick(email_address=subscriber.email, newsletter=newsletter)
        tracker.save()

    return redirect(next)
