from django.conf import settings

from .models import Subscriber

if not settings.DEBUG:  # Anymail only available in production
    from anymail.signals import tracking
    from django.dispatch import receiver

    @receiver(tracking)  # add weak=False if inside some other function/class
    def handle_tracking(sender, event, esp_name, **kwargs):
        def _get_subscriber(email):
            try:
                subscriber = Subscriber.objects.get(email=email)
            except Subscriber.DoesNotExist:
                subscriber = None
            return subscriber

        if event.event_type == "bounced":
            subscriber = _get_subscriber(event.recipient)
            if subscriber:
                subscriber.bounced = True
                subscriber.save()
                print(f"Email to {subscriber} bounced; removed from mailing list")
            else:
                print(f"No subscriber found for bounced email: {subscriber}")

        if event.event_type == "complained":
            subscriber = _get_subscriber(event.recipient)
            if subscriber:
                subscriber.complained = True
                subscriber.save()
                print(f"Email to {subscriber} complained; removed from mailing list")
            else:
                print(f"No subscriber found for complaint: {subscriber}")
