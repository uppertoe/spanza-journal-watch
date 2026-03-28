from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from .forms import SubscriberForm
from .models import Subscriber
from .tasks import send_confirmation_email


def success(request):
    messages_to_render = messages.get_messages(request)
    return render(request, "newsletter/success.html", {"messages": messages_to_render})


def _unsubscribe_subscriber(request, subscriber):
    subscriber.subscribed = False
    subscriber.save(update_fields=["subscribed", "modified"])
    request.session["subscribed"] = False


@csrf_exempt  # Allow POST for list-unsubscribe-post
def unsubscribe(request, unsubscribe_token):
    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
    except Subscriber.DoesNotExist:
        if request.method == "POST":
            return HttpResponse(status=200)  # Silent success for mailbox-provider one-click POST
        messages.error(request, "Invalid unsubscribe link.")
        return redirect("home")

    if request.method == "POST":
        _unsubscribe_subscriber(request, subscriber)
        return HttpResponse(status=200)  # Immediate success, no redirect, for one-click unsubscribe POST

    # For manual GET-based unsubscribe with confirmation UI
    context = {"unsubscribe_token": unsubscribe_token, "email": subscriber.email}
    return render(request, "newsletter/unsubscribe.html", context)


def confirm_unsubscribe(request, unsubscribe_token):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
        _unsubscribe_subscriber(request, subscriber)
        messages.warning(request, f"'{subscriber.email}' been unsubscribed successfully.")

    except Subscriber.DoesNotExist:
        messages.error(request, "Invalid unsubscribe link.")

    return redirect("home")


def subscribe(request):
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request")

    if request.method == "POST":
        form = SubscriberForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]

            # Check if email exists
            subscriber = Subscriber.objects.filter(email__icontains=email).first()
            if subscriber:
                subscriber.subscribed = True
                subscriber.save()
                messages.success(request, f"'{email}' subscribed successfully.")
            else:
                subscriber = form.save()
                messages.success(request, f"'{email}' subscribed successfully.")

            # Set subscribed flag in session
            request.session["subscribed"] = True
            request.session["subscriber_id"] = subscriber.pk

            # Send confirmation email
            send_confirmation_email.delay(subscriber.pk)

            return redirect("newsletter:success")
    else:
        form = SubscriberForm()

    return render(request, "newsletter/subscribe.html", {"form": form})
