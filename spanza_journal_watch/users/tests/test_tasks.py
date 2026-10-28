import pytest
from celery.result import EagerResult

from spanza_journal_watch.users.tasks import get_users_count
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_count(settings):
    """A basic test to execute the get_users_count Celery task."""
    baseline_count = get_users_count()
    UserFactory.create_batch(3)
    settings.CELERY_TASK_ALWAYS_EAGER = True
    task_result = get_users_count.delay()
    assert isinstance(task_result, EagerResult)
    assert task_result.result == baseline_count + 3


class TestPurgeUnconfirmedAccounts:
    @staticmethod
    def _account(email, *, days_old=8, verified=False, **fields):
        from datetime import timedelta

        from allauth.account.models import EmailAddress
        from django.utils import timezone

        user = UserFactory(email=email, **fields)
        user.__class__.objects.filter(pk=user.pk).update(
            date_joined=timezone.now() - timedelta(days=days_old), last_login=None
        )
        EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
        return user

    def test_only_old_unconfirmed_accounts_are_deleted(self):
        from django.contrib.auth import get_user_model

        from spanza_journal_watch.users.tasks import purge_unconfirmed_accounts

        stale = self._account("stale@example.org")
        recent = self._account("recent@example.org", days_old=2)
        confirmed = self._account("confirmed@example.org", verified=True)
        staff = self._account("staff@example.org", is_staff=True)

        assert purge_unconfirmed_accounts() == 1

        remaining = set(get_user_model().objects.values_list("pk", flat=True))
        assert stale.pk not in remaining
        assert {recent.pk, confirmed.pk, staff.pk} <= remaining

    def test_accounts_linked_to_editorial_records_are_kept(self):
        from datetime import timedelta

        from django.utils import timezone

        from spanza_journal_watch.backend.models import ChiefEditorInvite, IssueContributor
        from spanza_journal_watch.submissions.models import Author, Issue
        from spanza_journal_watch.users.tasks import unconfirmed_accounts

        author = self._account("author@example.org")
        Author.objects.create(name="An Author", email="Author@example.org")
        invited = self._account("invited@example.org")
        IssueContributor.objects.create(issue=Issue.objects.create(name="October 2026"), email="invited@example.org")
        chief = self._account("chief@example.org")
        ChiefEditorInvite.objects.create(
            email="chief@example.org", token_hash="c" * 64, expires_at=timezone.now() + timedelta(days=3)
        )
        signed_in = self._account("signed-in@example.org")
        signed_in.__class__.objects.filter(pk=signed_in.pk).update(last_login=timezone.now())

        purgeable = set(unconfirmed_accounts().values_list("pk", flat=True))
        assert not purgeable & {author.pk, invited.pk, chief.pk, signed_in.pk}
