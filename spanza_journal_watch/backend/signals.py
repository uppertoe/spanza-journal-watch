import logging
import re

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _normalize_subject(subject):
    """Strip Re:/Fwd: prefixes for thread grouping."""
    return re.sub(r"^(re|fwd?)\s*:\s*", "", subject or "", flags=re.IGNORECASE).strip()


if not settings.DEBUG:  # Anymail only available in production
    try:
        from anymail.signals import inbound  # type: ignore[import-not-found]
        from django.dispatch import receiver
    except ModuleNotFoundError:
        inbound = None

    if inbound:

        @receiver(inbound)
        def handle_inbound_email(sender, event, esp_name, **kwargs):
            from .models import EmailThread, InboundEmail, SentEmail

            message = event.message
            msg_id = (getattr(message, "message_id", None) or "").strip()
            in_reply_to = (message.headers.get("In-Reply-To") or "").strip() if message.headers else ""
            received_at = message.date or timezone.now()

            # Try to link to an existing thread via In-Reply-To matching a sent message_id
            thread = None
            if in_reply_to:
                sent = SentEmail.objects.filter(message_id=in_reply_to).first()
                if sent:
                    thread = sent.thread

            if thread is None:
                thread = EmailThread.objects.create(
                    external_address=message.envelope_sender or "",
                    subject=_normalize_subject(message.subject),
                    last_message_at=received_at,
                    has_unread=True,
                )
            else:
                thread.last_message_at = received_at
                thread.has_unread = True
                thread.save(update_fields=["last_message_at", "has_unread"])

            recipients = ", ".join(str(r) for r in (message.to or []))
            InboundEmail.objects.create(
                thread=thread,
                sender=message.envelope_sender,
                recipient=message.envelope_recipient,
                header_sender=str(message.from_email) if message.from_email else "",
                header_recipients=recipients,
                subject=message.subject,
                body=message.text,
                body_html=message.html,
                sent_timestamp=message.date,
                attachments=bool(message.attachments),
                email_file=event.event_id,
                message_id=msg_id,
                in_reply_to=in_reply_to,
            )

            logger.info("Email received from %s: %s (thread %s)", message.envelope_sender, event.event_id, thread.pk)
