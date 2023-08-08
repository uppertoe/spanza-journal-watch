from django.contrib import messages
from django.shortcuts import redirect

from .models import Subscriber, UnsubscribeTokenGenerator


def unsubscribe(request, unsubscribe_token):
    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=unsubscribe_token)
        token_generator = UnsubscribeTokenGenerator()
        if token_generator.check_token(subscriber, unsubscribe_token):
            subscriber.is_subscribed = False
            subscriber.save()
            messages.success(request, "You have been unsubscribed successfully.")
        else:
            messages.error(request, "Invalid unsubscribe link.")
    except Subscriber.DoesNotExist:
        messages.error(request, "Subscriber does not exist.")

    return redirect("home")
