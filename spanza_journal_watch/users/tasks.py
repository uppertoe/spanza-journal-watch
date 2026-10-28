import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from config import celery_app

User = get_user_model()
logger = logging.getLogger(__name__)

UNCONFIRMED_ACCOUNT_DAYS = 7


@celery_app.task()
def get_users_count():
    """A pointless Celery task to demonstrate usage."""
    return User.objects.count()


def unconfirmed_accounts(now=None):
    """Accounts whose address was never confirmed, a week or more after sign-up.

    Staff, anyone who has signed in, and anyone an editor has linked to an
    author record, an issue or an invitation are left alone.
    """
    from allauth.account.models import EmailAddress

    from spanza_journal_watch.backend.models import ChiefEditorInvite, IssueContributor
    from spanza_journal_watch.submissions.models import Author

    now = now or timezone.now()
    verified = EmailAddress.objects.filter(user=OuterRef("pk"), verified=True)
    linked_contributor = IssueContributor.objects.filter(Q(user=OuterRef("pk")) | Q(email__iexact=OuterRef("email")))
    linked_author = Author.objects.filter(Q(user=OuterRef("pk")) | Q(email__iexact=OuterRef("email")))
    pending_chief_invite = ChiefEditorInvite.objects.filter(
        email__iexact=OuterRef("email"), consumed_at__isnull=True, expires_at__gt=now
    )
    return (
        User.objects.filter(
            date_joined__lt=now - timedelta(days=UNCONFIRMED_ACCOUNT_DAYS),
            is_staff=False,
            is_superuser=False,
            last_login__isnull=True,
        )
        .exclude(Exists(verified))
        .exclude(Exists(linked_contributor))
        .exclude(Exists(linked_author))
        .exclude(Exists(pending_chief_invite))
    )


@celery_app.task()
def purge_unconfirmed_accounts():
    """Delete accounts whose address was not confirmed within a week of sign-up.

    These accounts have never signed in, so they hold no reviews, comments or
    reading. A plain delete is used: delete_account would also unsubscribe the
    address from the newsletter, which the real owner of the address may want.
    """
    deleted = 0
    for user in unconfirmed_accounts():
        user_pk = user.pk
        user.delete()
        deleted += 1
        logger.info("Deleted unconfirmed account %s", user_pk)
    return deleted
