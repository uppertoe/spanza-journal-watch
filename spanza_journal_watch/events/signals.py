from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from spanza_journal_watch.utils.cache import bump_content_cache_version

from .models import LiveSession, LiveSessionReading


@receiver(post_save, sender=LiveSession)
@receiver(post_delete, sender=LiveSession)
@receiver(post_save, sender=LiveSessionReading)
@receiver(post_delete, sender=LiveSessionReading)
def invalidate_content_cache_on_session_change(sender, **kwargs):
    if kwargs.get("raw"):
        return
    bump_content_cache_version()
