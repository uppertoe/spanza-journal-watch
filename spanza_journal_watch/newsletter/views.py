from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from spanza_journal_watch.analytics.models import AnalyticsEvent

from .cookies import JW_SUB_COOKIE_NAME, set_subscribed_cookie
from .forms import SubscriberForm
from .models import Subscriber
from .tasks import send_confirmation_email


def _render_masthead_button_oob(request, is_known_subscriber):
    """Render the masthead button as an OOB swap fragment."""
    return render_to_string(
        "fragments/masthead_profile_button.html",
        {"is_known_subscriber": is_known_subscriber, "oob": True},
        request=request,
    )


def success(request):
    messages_to_render = messages.get_messages(request)
    return render(request, "newsletter/success.html", {"messages": messages_to_render})


def _unsubscribe_subscriber(request, subscriber):
    subscriber.subscribed = False
    subscriber.save(update_fields=["subscribed", "modified"])
    for key in ("subscribed", "subscriber_id", "subscriber_email"):
        request.session.pop(key, None)


def _get_subscriber_by_unsubscribe_token(unsubscribe_token):
    return Subscriber.objects.filter(unsubscribe_token=unsubscribe_token).order_by("pk").first()


def _sync_exact_email_subscription_state(email, subscribed):
    subscribers = Subscriber.by_email(email)
    if subscribers.exists():
        subscribers.update(subscribed=subscribed, modified=timezone.now())
    return subscribers


@csrf_exempt  # Allow POST for list-unsubscribe-post
def unsubscribe(request, unsubscribe_token):
    subscriber = _get_subscriber_by_unsubscribe_token(unsubscribe_token)
    if not subscriber:
        if request.method == "POST":
            return HttpResponse(status=200)  # Silent success for mailbox-provider one-click POST
        messages.error(request, "Invalid unsubscribe link.")
        return redirect("home")

    if request.method == "POST":
        _unsubscribe_subscriber(request, subscriber)
        response = HttpResponse(status=200)  # Immediate success, no redirect, for one-click unsubscribe POST
        response.delete_cookie(JW_SUB_COOKIE_NAME)
        return response

    # For manual GET-based unsubscribe with confirmation UI
    context = {"unsubscribe_token": unsubscribe_token, "email": subscriber.email}
    return render(request, "newsletter/unsubscribe.html", context)


def confirm_unsubscribe(request, unsubscribe_token):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    subscriber = _get_subscriber_by_unsubscribe_token(unsubscribe_token)
    if subscriber:
        _unsubscribe_subscriber(request, subscriber)
        messages.warning(request, f"'{subscriber.email}' been unsubscribed successfully.")
    else:
        messages.error(request, "Invalid unsubscribe link.")

    response = redirect("home")
    response.delete_cookie(JW_SUB_COOKIE_NAME)
    return response


@login_required
@require_POST
def toggle_subscription(request):
    """Toggle the current user's newsletter subscription."""
    subscriber = Subscriber.first_by_email(request.user.email)
    if subscriber:
        if not subscriber.user:
            subscriber.user = request.user
        subscriber.subscribed = not subscriber.subscribed
        subscriber.save(update_fields=["subscribed", "user", "modified"])
        _sync_exact_email_subscription_state(request.user.email, subscriber.subscribed)
        request.session["subscribed"] = subscriber.subscribed
    else:
        subscriber = Subscriber.objects.create(
            email=request.user.email,
            user=request.user,
            subscribed=True,
        )
        request.session["subscribed"] = True
        send_confirmation_email.delay(subscriber.pk)

    return render(
        request,
        "fragments/user_profile_newsletter_toggle.html",
        {
            "user_is_subscribed": subscriber.subscribed,
        },
    )


def subscribe(request):
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request")

    is_drawer = request.POST.get("source") == "drawer" or request.GET.get("source") == "drawer"

    if request.method == "POST":
        form = SubscriberForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]

            # Check if email exists
            existing = Subscriber.first_by_email(email)
            if existing:
                existing.subscribed = True
                existing.save(update_fields=["subscribed", "modified"])
                _sync_exact_email_subscription_state(email, True)
                subscriber = existing
            else:
                subscriber = form.save()
            messages.success(request, f"'{email}' subscribed successfully.")

            # Set subscribed flag in session
            request.session["subscribed"] = True
            request.session["subscriber_id"] = subscriber.pk
            request.session["subscriber_email"] = email

            AnalyticsEvent.record_event(
                event_type=AnalyticsEvent.EventType.NEWSLETTER_SUBSCRIBE,
                request=request,
                subscriber_id=subscriber.pk,
                source="drawer" if is_drawer else "subscribe_form",
                metadata={"resubscribe": bool(existing)},
            )

            # Send confirmation email
            send_confirmation_email.delay(subscriber.pk)

            if is_drawer:
                success_html = render_to_string(
                    "newsletter/_drawer_subscribed_panel.html",
                    {
                        "subscriber_email": email,
                        "can_unsubscribe_from_drawer": True,
                        "just_subscribed": True,
                    },
                    request=request,
                )
                oob_html = _render_masthead_button_oob(request, is_known_subscriber=True)
                response = HttpResponse(success_html + oob_html)
                set_subscribed_cookie(response)
                return response

            response = redirect("newsletter:success")
            set_subscribed_cookie(response)
            return response
    else:
        form = SubscriberForm()

    if is_drawer:
        return render(request, "newsletter/_drawer_subscribe_form.html", {"form": form})
    return render(request, "newsletter/subscribe.html", {"form": form})


@require_POST
def drawer_unsubscribe(request):
    """Unsubscribe the session-linked subscriber from the drawer."""
    if request.headers.get("HX-Request") != "true":
        return HttpResponseBadRequest("Bad Request")

    subscriber_id = request.session.get("subscriber_id")
    if subscriber_id:
        subscriber = Subscriber.objects.filter(pk=subscriber_id).first()
        if subscriber:
            subscriber.subscribed = False
            subscriber.save(update_fields=["subscribed", "modified"])
            _sync_exact_email_subscription_state(subscriber.email, False)

    # Forget session state
    for key in ("subscribed", "subscriber_id", "subscriber_email"):
        request.session.pop(key, None)

    success_html = render_to_string(
        "newsletter/_drawer_unsubscribe_success.html",
        {"form": SubscriberForm()},
        request=request,
    )
    oob_html = _render_masthead_button_oob(request, is_known_subscriber=False)
    response = HttpResponse(success_html + oob_html)
    response.delete_cookie(JW_SUB_COOKIE_NAME)
    return response
