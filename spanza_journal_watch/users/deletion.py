"""Closing a reader's own account.

Published reviews belong to the journal, so they are never deleted with an
account. The author record behind them is made anonymous instead: the name,
address, health services and photograph go, and the reviews are credited to
"Anonymous". The newsletter address is kept, unsubscribed, so that a later
membership import does not mail it again.
"""

from django.db import transaction

from spanza_journal_watch.utils.image_variants import image_variant_name

ANONYMOUS_AUTHOR_NAME = "Anonymous"
# Matches the variant widths Author.save asks celery_resize_image for.
PROFILE_IMAGE_VARIANT_WIDTHS = (200,)


def can_delete_own_account(user):
    """Editorial staff hold permissions and OAuth applications that cascade, so an administrator removes them."""
    return not (user.is_staff or user.is_superuser)


def published_review_count(user):
    from spanza_journal_watch.submissions.models import Review

    return Review.objects.filter(author__user=user, active=True).count()


def _delete_profile_image(author):
    image = author.profile_image
    if not image:
        return
    storage, path = image.storage, image.name
    for width in PROFILE_IMAGE_VARIANT_WIDTHS:
        variant_path = image_variant_name(path, width)
        if storage.exists(variant_path):
            storage.delete(variant_path)
    image.delete(save=False)


def anonymise_author(author):
    _delete_profile_image(author)
    author.title = ""
    author.name = ANONYMOUS_AUTHOR_NAME
    author.email = None
    author.user = None
    author.anonymous = True
    author.show_profile_image = False
    author.profile_image = None
    author.slug = ""  # Regenerated from the new name, so the old address no longer names the person
    author.save()
    author.health_services.clear()


def delete_account(user):
    """Delete the account and everything held for it, keeping published reviews under an anonymous author."""
    from spanza_journal_watch.newsletter.models import Subscriber
    from spanza_journal_watch.submissions.models import Author, Comment

    with transaction.atomic():
        # Author.user cascades, which would take the reviews with it.
        for author in Author.objects.filter(user=user):
            anonymise_author(author)
        Comment.objects.filter(author=user).update(author=None)

        subscribers = Subscriber.objects.filter(user=user) | Subscriber.by_email(user.email)
        for subscriber in subscribers.distinct():
            subscriber.user = None
            subscriber.subscribed = False
            subscriber.save()

        user.delete()
