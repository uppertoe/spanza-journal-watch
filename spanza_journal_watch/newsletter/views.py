from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from .forms import SubscriberForm
from .models import Subscriber
from .tasks import reset_unsubscribe_token, send_confirmation_email


def success(request):
    messages_to_render = messages.get_messages(request)
    return render(request, "newsletter/success.html", {"messages": messages_to_render})


@csrf_exempt  # Allow POST for list-unsubscribe-post
def unsubscribe(request, unsubscribe_token):
    # Support single-click unsubscribe for POST requests
    if request.method == "POST":
        return redirect("newsletter:confirm-unsubscribe", unsubscribe_token=unsubscribe_token)

    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
    except Subscriber.DoesNotExist:
        messages.error(request, "Invalid unsubscribe link.")
        return redirect("home")

    context = {"unsubscribe_token": unsubscribe_token, "email": subscriber.email}

    return render(request, "newsletter/unsubscribe.html", context)


@csrf_exempt  # Allow POST for list-unsubscribe-postt
def confirm_unsubscribe(request, unsubscribe_token):
    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
        subscriber.subscribed = False
        subscriber.save()
        messages.warning(request, f"'{subscriber.email}' been unsubscribed successfully.")

        # Set the subscribed flag in session
        request.session["subscribed"] = False

        # Reset the unsubscribe token in 3 minutes
        # Ensures that repeated unsubscribe attempts are seen to succeed
        reset_unsubscribe_token.apply_async((subscriber.pk,), countdown=3 * 60)

    except Subscriber.DoesNotExist:
        messages.error(request, "Invalid unsubscribe link.")

    # Return 204 for list-unsubscribe-post
    if request.method == "POST":
        return HttpResponse(status=204)

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
