from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import SubscriberForm
from .models import Subscriber


def success(request):
    messages_to_render = messages.get_messages(request)
    return render(request, "newsletter/success.html", {"messages": messages_to_render})


def unsubscribe(request, unsubscribe_token):
    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
        if subscriber:
            subscriber.subscribed = False
            subscriber.unsubscribe_token = ""
            subscriber.save()
            messages.success(request, f"{subscriber.email.capitalize()} been unsubscribed successfully.")
        else:
            messages.error(request, "Invalid unsubscribe link.")
    except Subscriber.DoesNotExist:
        messages.error(request, "Subscriber does not exist.")

    return redirect("newsletter:success")


def subscribe(request):
    if request.method == "POST":
        form = SubscriberForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]

            # Check if email exists
            existing_subscriber = Subscriber.objects.filter(email=email).first()
            if existing_subscriber:
                existing_subscriber.subscribed = True
                existing_subscriber.save()
                messages.success(request, f"'{email}' updated successfully.")
            else:
                form.save()
                messages.success(request, f"'{email}' subscribed successfully.")
            return redirect("newsletter:success")
    else:
        form = SubscriberForm()

    return render(request, "newsletter/subscribe.html", {"form": form})