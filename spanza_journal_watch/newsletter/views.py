from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render

from .forms import SubscriberForm
from .models import Subscriber
from .tasks import send_confirmation_email


def success(request):
    messages_to_render = messages.get_messages(request)
    return render(request, "newsletter/success.html", {"messages": messages_to_render})


def unsubscribe(request, unsubscribe_token):
    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
        subscriber.subscribed = False
        subscriber.unsubscribe_token = ""
        subscriber.save()
        messages.warning(request, f"'{subscriber.email}' been unsubscribed successfully.")

        # Set the subscribed flag in session
        request.session["subscribed"] = False

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
            subscriber = Subscriber.objects.filter(email=email).first()
            if subscriber:
                subscriber.subscribed = True
                subscriber.save()
                messages.success(request, f"'{email}' subscribed successfully.")
            else:
                subscriber = form.save()
                messages.success(request, f"'{email}' subscribed successfully.")

            # Set subscribed flag in session
            request.session["subscribed"] = True

            # Send confirmation email
            send_confirmation_email.delay(subscriber.pk)

            return redirect("newsletter:success")
    else:
        form = SubscriberForm()

    return render(request, "newsletter/subscribe.html", {"form": form})
