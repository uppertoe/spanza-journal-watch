from django.conf import settings

if not settings.DEBUG:  # Anymail only available in production
    from anymail.signals import tracking
    from django.dispatch import receiver

    @receiver(tracking)  # add weak=False if inside some other function/class
    def handle_tracking(sender, event, esp_name, **kwargs):
        if esp_name == "Amazon SES":
            try:
                message_tags = {name: values[0] for name, values in event.esp_event["mail"]["tags"].items()}
            except KeyError:
                message_tags = None  # SES Notification (not Event Publishing) event
            print(
                "Message {} to {} event {}: Message Tags {!r}".format(
                    event.message_id, event.recipient, event.event_type, message_tags
                )
            )
