from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from spanza_journal_watch.utils.cache import bump_content_cache_version

from .models import Article, Author, Issue, Review, Tag


@receiver(post_save, sender=Article)
@receiver(post_delete, sender=Article)
@receiver(post_save, sender=Author)
@receiver(post_delete, sender=Author)
@receiver(post_save, sender=Issue)
@receiver(post_delete, sender=Issue)
@receiver(post_save, sender=Review)
@receiver(post_delete, sender=Review)
@receiver(post_save, sender=Tag)
@receiver(post_delete, sender=Tag)
def invalidate_content_cache_on_model_change(sender, **kwargs):
    if kwargs.get("raw"):
        return
    bump_content_cache_version()


@receiver(m2m_changed, sender=Issue.reviews.through)
def invalidate_content_cache_on_issue_reviews_change(sender, action, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"}:
        bump_content_cache_version()


@receiver(m2m_changed, sender=Tag.articles.through)
def invalidate_content_cache_on_tag_articles_change(sender, action, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"}:
        bump_content_cache_version()
