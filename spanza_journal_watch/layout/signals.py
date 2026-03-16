from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from spanza_journal_watch.utils.cache import bump_content_cache_version

from .models import FeatureArticle, Homepage, PageHeader


@receiver(post_save, sender=FeatureArticle)
@receiver(post_delete, sender=FeatureArticle)
@receiver(post_save, sender=Homepage)
@receiver(post_delete, sender=Homepage)
@receiver(post_save, sender=PageHeader)
@receiver(post_delete, sender=PageHeader)
def invalidate_content_cache_on_layout_change(sender, **kwargs):
    if kwargs.get("raw"):
        return
    bump_content_cache_version()
