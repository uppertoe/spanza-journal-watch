import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.backend.models import (
    ChiefEditorInvite,
    IssueContributor,
    IssueContributorInvite,
    PubmedArticle,
    PubmedArticleUserState,
)
from spanza_journal_watch.cpd.models import CPDReport
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Author, Comment, HealthService, Issue, Review
from spanza_journal_watch.users.deletion import can_delete_own_account, delete_account, published_review_count
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def contributor():
    user = UserFactory(email="jane@example.org", name="Jane Citizen")
    author = Author.objects.create(title="Dr", name="Jane Citizen", email="jane@example.org", user=user)
    author.health_services.add(HealthService.objects.create(name="Children's Hospital"))
    article = PubmedArticle.objects.create(pmid="9100001", title="Caudal blocks in infants")
    Review.objects.create(article=article, body="Body text", author=author, active=True)
    return user


def test_reviews_stay_published_under_an_anonymous_author(contributor):
    author = Author.objects.get(user=contributor)
    old_slug = author.slug

    delete_account(contributor)

    author.refresh_from_db()
    assert author.user is None
    assert author.anonymous
    assert str(author) == "Anonymous"
    assert author.email is None
    assert author.slug != old_slug and "jane" not in author.slug
    assert not author.health_services.exists()
    assert Review.objects.filter(author=author, active=True).count() == 1
    assert not User.objects.filter(pk=contributor.pk).exists()


def test_personal_records_go_with_the_account(contributor):
    article = PubmedArticle.objects.get(pmid="9100001")
    PubmedArticleUserState.objects.create(user=contributor, article=article)
    CPDReport.objects.create(user=contributor, date_from="2026-01-01", date_to="2026-12-31")
    comment = Comment.objects.create(body="Worth a review", article=article, author=contributor)

    delete_account(contributor)

    assert not PubmedArticleUserState.objects.filter(article=article).exists()
    assert not CPDReport.objects.exists()
    comment.refresh_from_db()
    assert comment.author is None


def test_newsletter_address_is_kept_unsubscribed(contributor):
    Subscriber.objects.create(email="JANE@example.org", subscribed=True)

    delete_account(contributor)

    subscriber = Subscriber.objects.get()
    assert not subscriber.subscribed
    assert subscriber.user is None


def test_review_count_covers_only_published_reviews(contributor):
    author = Author.objects.get(user=contributor)
    article = PubmedArticle.objects.create(pmid="9100002", title="Draft")
    Review.objects.create(article=article, body="Draft", author=author, active=False)
    assert published_review_count(contributor) == 1


def test_editorial_staff_cannot_delete_their_own_account():
    assert can_delete_own_account(UserFactory(is_staff=False))
    assert not can_delete_own_account(UserFactory(is_staff=True))
    assert not can_delete_own_account(UserFactory(is_superuser=True))


def test_unlinked_author_under_the_same_address_is_anonymised(contributor):
    article = PubmedArticle.objects.create(pmid="9100003", title="Spinal anaesthesia")
    unlinked = Author.objects.create(name="J. Citizen", email="JANE@example.org")
    Review.objects.create(article=article, body="Body", author=unlinked, active=True)
    other = Author.objects.create(name="Someone Else", email="other@example.org")

    delete_account(contributor)

    unlinked.refresh_from_db()
    other.refresh_from_db()
    assert unlinked.anonymous and unlinked.email is None
    assert Review.objects.filter(author=unlinked).exists()
    assert other.name == "Someone Else"


def test_content_cache_is_refreshed_after_the_author_is_anonymised(contributor, django_capture_on_commit_callbacks):
    with patch("spanza_journal_watch.users.deletion.bump_content_cache_version") as bump:
        with django_capture_on_commit_callbacks(execute=True):
            delete_account(contributor)
    bump.assert_called()


def test_contributor_places_and_invitations_are_removed(contributor, django_capture_on_commit_callbacks):
    issue = Issue.objects.create(name="October 2026", active=False)
    linked = IssueContributor.objects.create(
        issue=issue,
        email="jane@example.org",
        user=contributor,
        status=IssueContributor.Status.ACTIVE,
        planka_user_id="u-1",
        planka_membership_id="m-1",
    )
    later_issue = Issue.objects.create(name="November 2026", active=False)
    invited = IssueContributor.objects.create(issue=later_issue, email="JANE@example.org")
    expires = timezone.now() + datetime.timedelta(days=7)
    IssueContributorInvite.objects.create(contributor=invited, token_hash="a" * 64, expires_at=expires)
    ChiefEditorInvite.objects.create(email="jane@example.org", token_hash="b" * 64, expires_at=expires)
    kept = IssueContributor.objects.create(issue=issue, email="other@example.org")

    contributor_pk = contributor.pk
    with patch("spanza_journal_watch.backend.tasks.withdraw_planka_access_for_deleted_account.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            delete_account(contributor)

    assert list(IssueContributor.objects.all()) == [kept]
    assert not IssueContributorInvite.objects.exists()
    assert not ChiefEditorInvite.objects.exists()
    user_pk, memberships = delay.call_args.args
    assert user_pk == contributor_pk
    assert memberships == [
        {
            "issue_id": linked.issue_id,
            "planka_user_id": "u-1",
            "membership_id": "m-1",
            "instructions_membership_id": "",
        }
    ]


def test_planka_is_not_contacted_for_an_account_without_planka_access(contributor, django_capture_on_commit_callbacks):
    with patch("spanza_journal_watch.backend.tasks.withdraw_planka_access_for_deleted_account.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            delete_account(contributor)
    delay.assert_not_called()


def test_profile_photo_is_deleted_only_after_commit(contributor, django_capture_on_commit_callbacks):
    author = Author.objects.get(user=contributor)
    storage = Author._meta.get_field("profile_image").storage
    path = storage.save("profile_images/jane-test.jpg", ContentFile(b"photo"))
    Author.objects.filter(pk=author.pk).update(profile_image=path)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        delete_account(contributor)
        assert storage.exists(path)
    for callback in callbacks:
        callback()

    assert not storage.exists(path)


def test_a_storage_error_after_commit_does_not_undo_the_deletion(contributor, django_capture_on_commit_callbacks):
    author = Author.objects.get(user=contributor)
    Author.objects.filter(pk=author.pk).update(profile_image="profile_images/jane-missing.jpg")

    contributor_pk = contributor.pk
    storage = Author._meta.get_field("profile_image").storage
    with patch.object(storage, "exists", side_effect=OSError("storage unavailable")) as exists:
        with django_capture_on_commit_callbacks(execute=True):
            delete_account(contributor)

    exists.assert_called()
    assert not User.objects.filter(pk=contributor_pk).exists()


class TestDeleteAccountPage:
    def test_contributor_is_warned_about_their_reviews(self, client, contributor):
        client.force_login(contributor)
        response = client.get(reverse("users:delete"))
        assert response.status_code == 200
        assert "You have written 1 published review for Journal Watch." in response.content.decode()

    def test_reader_without_reviews_sees_no_review_warning(self, client):
        client.force_login(UserFactory(is_staff=False))
        content = client.get(reverse("users:delete")).content.decode()
        assert "Delete my account" in content
        assert "published review" not in content

    def test_post_deletes_the_account_and_signs_out(self, client, contributor):
        client.force_login(contributor)
        response = client.post(reverse("users:delete"))
        assert response.status_code == 302
        assert not User.objects.filter(pk=contributor.pk).exists()
        assert "_auth_user_id" not in client.session

    def test_a_failed_deletion_leaves_the_reader_signed_in(self, client, contributor):
        client.force_login(contributor)
        with patch("spanza_journal_watch.users.deletion.delete_account", side_effect=RuntimeError("database")):
            with pytest.raises(RuntimeError):
                client.post(reverse("users:delete"))
        assert client.session["_auth_user_id"] == str(contributor.pk)

    def test_staff_are_sent_to_an_administrator(self, client):
        staff = UserFactory(is_staff=True)
        client.force_login(staff)
        assert "Delete my account" not in client.get(reverse("users:delete")).content.decode()
        client.post(reverse("users:delete"))
        assert User.objects.filter(pk=staff.pk).exists()

    def test_signed_out_visitors_are_sent_to_sign_in(self, client):
        assert client.get(reverse("users:delete")).status_code == 302
