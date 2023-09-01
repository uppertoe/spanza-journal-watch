from django.contrib.staticfiles import finders
from django.core.exceptions import MultipleObjectsReturned
from django.http import HttpResponse
from django.shortcuts import redirect

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


def track_email_open(request):
    email = request.GET.get("email")
    token = request.GET.get("token")

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


def track_email_link(request, newsletter_token):
    next = request.GET.get("next")
    email = request.GET.get("email")

    newsletter = _get_newsletter(newsletter_token)
    subscriber = _get_subscriber(email)

    if newsletter and subscriber:
        tracker = NewsletterClick(subscriber=subscriber, newsletter=newsletter)
        tracker.save()

        # Identify the subscriber in the session
        request.session["subscriber_id"] = subscriber.pk

    return redirect(next)


def page_view(request, model=None, slug=None):
    if model == "review":
        try:
            review = Review.objects.get(slug=slug)
            subscriber_id = request.session.get("subscriber_id")
            print(f"Here's the ID: {subscriber_id}")
            PageView.record_view(review, subscriber_id)
        except (Review.DoesNotExist, MultipleObjectsReturned) as e:
            print(e)

    return HttpResponse("")
