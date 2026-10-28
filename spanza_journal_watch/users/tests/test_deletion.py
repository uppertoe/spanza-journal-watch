import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from spanza_journal_watch.backend.models import PubmedArticle, PubmedArticleUserState
from spanza_journal_watch.cpd.models import CPDReport
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Author, Comment, HealthService, Review
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

    def test_staff_are_sent_to_an_administrator(self, client):
        staff = UserFactory(is_staff=True)
        client.force_login(staff)
        assert "Delete my account" not in client.get(reverse("users:delete")).content.decode()
        client.post(reverse("users:delete"))
        assert User.objects.filter(pk=staff.pk).exists()

    def test_signed_out_visitors_are_sent_to_sign_in(self, client):
        assert client.get(reverse("users:delete")).status_code == 302
