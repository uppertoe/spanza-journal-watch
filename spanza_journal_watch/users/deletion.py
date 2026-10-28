"""Closing a reader's own account.

Published reviews belong to the journal, so they are never deleted with an
account. The author record behind them is made anonymous instead: the name,
address, health services and photograph go, and the reviews are credited to
"Anonymous". The newsletter address is kept, unsubscribed, so that a later
membership import does not mail it again. Contributor places on issues and
pending invitations are removed, and the person's Planka access is withdrawn
once the deletion has committed.
"""

import logging

from django.db import transaction
from django.db.models import Q

from spanza_journal_watch.utils.cache import bump_content_cache_version
from spanza_journal_watch.utils.image_variants import image_variant_name

logger = logging.getLogger(__name__)

ANONYMOUS_AUTHOR_NAME = "Anonymous"
# Matches the variant widths Author.save asks celery_resize_image for.
PROFILE_IMAGE_VARIANT_WIDTHS = (200,)


def can_delete_own_account(user):
    """Editorial staff hold permissions and OAuth applications that cascade, so an administrator removes them."""
    return not (user.is_staff or user.is_superuser)


def published_review_count(user):
    from spanza_journal_watch.submissions.models import Review

    return Review.objects.filter(author__user=user, active=True).count()


def _delete_stored_files(storage, paths):
    """Runs after commit, so a storage error leaves the account deleted and is only logged."""
    for path in paths:
        try:
            if storage.exists(path):
                storage.delete(path)
        except Exception:
            logger.exception("Could not delete a stored profile image file after account deletion")


def _delete_profile_image(author):
    """Delete the photograph and its resized copies once the surrounding transaction commits."""
    image = author.profile_image
    if not image:
        return
    storage, path = image.storage, image.name
    paths = [path] + [image_variant_name(path, width) for width in PROFILE_IMAGE_VARIANT_WIDTHS]
    transaction.on_commit(lambda: _delete_stored_files(storage, paths))


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


def _authors_for(user):
    """Author records linked to the account, and unlinked ones under the same address."""
    from spanza_journal_watch.submissions.models import Author

    matches = Q(user=user)
    if user.email:
        matches |= Q(user__isnull=True, email__iexact=user.email)
    return Author.objects.filter(matches)


def _remove_editorial_places(user):
    """Delete the account's contributor rows and pending invitations, returning what Planka needs.

    Contributor rows hold the person's address and name, and only their own
    invitations refer to them, so they are deleted rather than kept as revoked.
    """
    from spanza_journal_watch.backend.models import ChiefEditorInvite, IssueContributor

    matches = Q(user=user)
    if user.email:
        matches |= Q(email__iexact=user.email)
    contributors = IssueContributor.objects.filter(matches)
    memberships = [
        {
            "issue_id": contributor.issue_id,
            "planka_user_id": contributor.planka_user_id,
            "membership_id": contributor.planka_membership_id,
            "instructions_membership_id": contributor.planka_instructions_membership_id,
        }
        for contributor in contributors
        if contributor.planka_user_id or contributor.planka_membership_id
    ]
    contributors.delete()  # Their invitations cascade
    if user.email:
        ChiefEditorInvite.objects.filter(email__iexact=user.email, consumed_at__isnull=True).delete()
    return memberships


def delete_account(user):
    """Delete the account and everything held for it, keeping published reviews under an anonymous author."""
    from spanza_journal_watch.backend.tasks import withdraw_planka_access_for_deleted_account
    from spanza_journal_watch.newsletter.models import Subscriber
    from spanza_journal_watch.submissions.models import Comment

    user_pk = user.pk
    with transaction.atomic():
        # Author.user cascades, which would take the reviews with it.
        authors = list(_authors_for(user))
        for author in authors:
            anonymise_author(author)
        if authors:
            # Author.save bumps the version too, but a page cached before commit would keep the old name.
            transaction.on_commit(bump_content_cache_version)
        Comment.objects.filter(author=user).update(author=None)

        memberships = _remove_editorial_places(user)
        if memberships:
            transaction.on_commit(lambda: withdraw_planka_access_for_deleted_account.delay(user_pk, memberships))

        subscribers = Subscriber.objects.filter(user=user) | Subscriber.by_email(user.email)
        for subscriber in subscribers.distinct():
            subscriber.user = None
            subscriber.subscribed = False
            subscriber.save()

        user.delete()
