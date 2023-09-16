from django.conf import settings

from .forms import InboundAnymailEmailForm

if not settings.DEBUG:  # Anymail only available in production
    from anymail.signals import inbound
    from django.dispatch import receiver

    @receiver(inbound)  # add weak=False if inside some other function/class
    def handle_inbound_email(sender, event, esp_name, **kwargs):
        message = event.message

        email = {
            "sender": message.envelope_sender,
            "recipient": message.envelope_recipient,
            "header_sender": message.from_email,
            "header_recipients": message.to_email,
            "subject": message.subject,
            "body": message.text,
            "body_html": message.html,
            "sent_timestamp": message.date,
            "attachments": message.attachments,
            "email_file": event.event_id,  # Corresponds to S3 object
        }

        form = InboundAnymailEmailForm(email)

        if form.is_valid():
            form.save()

        print(f"Email received from {message.envelope_sender}: {event.event_id}")
