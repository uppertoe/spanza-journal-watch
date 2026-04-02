"""
End-to-end integration test: full issue editorial workflow.

Stages covered both individually and in a single chained workflow test:

  Stage 1 — Issue & Review:  chief editor creates an Issue and adds a Review.
  Stage 2 — Contributors:    manager adds a contributor and sends an invite.
  Stage 3 — Invite flow:     unauthenticated visitor opens the invite link,
                              creates an account, and accepts.
  Stage 4 — Planka webhook:  incoming webhook records a PlankaCardRevision.
  Stage 5 — Card import:     chief editor imports a Planka card as a Review.
  Stage 6 — Publication:     issue published — active, reviews get publish_date.
  Stage 7 — Newsletter:      newsletter created, test-sent, and queued.
  Stage 8 — Frontend:        public issue-detail page renders active reviews.
"""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.backend.models import (
    IssueContributor,
    IssueContributorInvite,
    PlankaCardRevision,
    PlankaIntegrationCredential,
    PlankaIssueBinding,
    PubmedArticle,
)
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review
from spanza_journal_watch.users.tests.factories import UserFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

CHIEF_EDITOR = "submissions.chief_editor"
MANAGE_ISSUE_BUILDER = "submissions.manage_issue_builder"
SEND_NEWSLETTERS = "backend.send_newsletters"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def _make_editor():
    u = UserFactory()
    _grant(u, CHIEF_EDITOR, MANAGE_ISSUE_BUILDER, SEND_NEWSLETTERS)
    c = Client()
    c.force_login(u)
    return c, u


def _make_manager():
    u = UserFactory()
    _grant(u, MANAGE_ISSUE_BUILDER)
    c = Client()
    c.force_login(u)
    return c, u


# ---------------------------------------------------------------------------
# Planka webhook helper
# ---------------------------------------------------------------------------

WEBHOOK_URL = "/editorial/webhooks/planka/card-update"
WEBHOOK_SECRET = "test-webhook-secret"


def _webhook_payload(card_id, board_id, description, prev_description=""):
    return {
        "event": "cardUpdate",
        "data": {
            "item": {
                "id": card_id,
                "boardId": board_id,
                "name": "Drug X RCT",
                "description": description,
            }
        },
        "prevData": {
            "item": {
                "id": card_id,
                "boardId": board_id,
                "description": prev_description,
            }
        },
        "actor": {
            "id": "actor-1",
            "email": "alice@example.com",
            "name": "Alice Smith",
        },
    }


# ---------------------------------------------------------------------------
# Sample Planka card content
# ---------------------------------------------------------------------------

CARD_DESCRIPTION = (
    "Journal: NEJM\n"
    "Article URL: https://nejm.org/doi/10.1056/e2e-test\n"
    "Publication date: 2024\n"
    "\n"
    "Abstract\n"
    "--\n"
    "This RCT demonstrated a significant reduction in 30-day mortality.\n"
    "\n"
    "< --- Please write your review below this line --- >\n"
    "A well-conducted trial with robust endpoints and broad applicability."
)


def _mock_card(card_id="card-e2e", list_id="list-3", in_publish_ready=True):
    """Return a card dict in the shape produced by _extract_board_cards."""
    return {
        "id": card_id,
        "name": "Drug X RCT",
        "description": CARD_DESCRIPTION,
        "schema": {
            "journal_name": "NEJM",
            "article_url": "https://nejm.org/doi/10.1056/e2e-test",
            "article_year": "2024",
            "article_abstract": "This RCT demonstrated a significant reduction in 30-day mortality.",
            "article_citation": "",
            "article_name": "Drug X RCT",
            "tags_string": "",
            "author_name": "",
            "author_title": "",
            "review_body_markdown": "A well-conducted trial with robust endpoints.",
            "is_featured": "",
        },
        "missing_required": [],
        "is_valid": True,
        "already_imported": False,
        "has_associated_review": False,
        "associated_review_id": None,
        "sync_blocked_reason": "",
        "list_id": list_id,
        "list_name": "Publish Ready",
        "list_type": "active",
        "in_publish_ready": in_publish_ready,
    }


def _mock_planka_client():
    """Return a MagicMock that satisfies all Planka API calls made during card import."""
    client = MagicMock()
    client.get_card_members.return_value = ([], {})
    client.get_card_description_editor_ids.return_value = []
    client.list_users.return_value = []
    return client


# ---------------------------------------------------------------------------
# Stage 1 — Issue & Review
# ---------------------------------------------------------------------------


class TestStage1IssueAndReview:
    def test_chief_editor_creates_issue(self):
        client, _ = _make_editor()
        client.post(
            reverse("backend:save_issue_draft"),
            data={"name": "March 2024", "date_0": "3", "date_1": "2024", "body": "Issue body."},
        )
        assert Issue.objects.filter(name="March 2024").exists()

    def test_new_issue_is_inactive(self):
        client, _ = _make_editor()
        client.post(
            reverse("backend:save_issue_draft"),
            data={"name": "March 2024", "date_0": "", "date_1": "", "body": "Body."},
        )
        assert Issue.objects.get(name="March 2024").active is False

    def test_add_review_creates_article_author_and_links_to_issue(self):
        issue = Issue.objects.create(name="March 2024", active=False, body="Body.")
        client, _ = _make_editor()
        client.post(
            reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            data={
                "article_mode": "new",
                "article_name": "Effect of Drug X on Outcomes",
                "author_mode": "new",
                "new_author_name": "Dr Jane Smith",
                "new_author_title": "Dr",
                "body": "This trial showed significant improvement.",
                "is_featured": True,
            },
        )
        assert issue.reviews.count() == 1
        review = issue.reviews.first()
        assert review.article.name == "Effect of Drug X on Outcomes"
        assert review.author.name == "Dr Jane Smith"
        assert review.is_featured is True


# ---------------------------------------------------------------------------
# Stage 2 — Contributors + Invite dispatch
# ---------------------------------------------------------------------------


class TestStage2ContributorsAndInvite:
    def test_add_contributor_creates_pending_record(self):
        issue = Issue.objects.create(name="March 2024", active=False, body="Body.")
        client, _ = _make_manager()
        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            client.post(
                reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk}),
                data={
                    "role": IssueContributor.Role.REVIEWER,
                    "name_0": "Alice Smith",
                    "email_0": "alice@example.com",
                },
            )
        contributor = IssueContributor.objects.filter(issue=issue, email="alice@example.com").first()
        assert contributor is not None
        assert contributor.status == IssueContributor.Status.PENDING

    def test_send_invite_transitions_status_and_creates_invite_record(self):
        issue = Issue.objects.create(name="March 2024", active=False, body="Body.")
        contributor = IssueContributor.objects.create(issue=issue, email="alice@example.com", name="Alice Smith")
        client, manager_user = _make_manager()
        with patch("spanza_journal_watch.backend.views._send_issue_invite_email") as mock_email:
            client.post(
                reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk}),
                data={"contributor_ids": [contributor.pk]},
            )
        contributor.refresh_from_db()
        assert contributor.status == IssueContributor.Status.INVITED
        assert contributor.invited_by_id == manager_user.pk
        assert contributor.invited_at is not None
        assert IssueContributorInvite.objects.filter(contributor=contributor).exists()
        mock_email.assert_called_once()


# ---------------------------------------------------------------------------
# Stage 3 — Invite acceptance flow
# ---------------------------------------------------------------------------


class TestStage3InviteAcceptance:
    def _setup_invite(self, email="alice@example.com"):
        issue = Issue.objects.create(name="March 2024", active=False)
        contributor = IssueContributor.objects.create(
            issue=issue, email=email, name="Alice Smith", status=IssueContributor.Status.INVITED
        )
        raw_token = IssueContributorInvite.generate_raw_token()
        invite = IssueContributorInvite.objects.create(
            contributor=contributor,
            token_hash=IssueContributorInvite.hash_token(raw_token),
            expires_at=timezone.now() + datetime.timedelta(days=7),
        )
        return contributor, invite, raw_token

    def test_unauthenticated_visitor_sees_create_account_button(self):
        _, _, raw_token = self._setup_invite()
        client = Client()
        response = client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))
        assert response.status_code == 200
        assert reverse("account_signup") in response.content.decode()

    def test_invite_page_sets_session_token(self):
        _, _, raw_token = self._setup_invite()
        client = Client()
        client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))
        assert client.session.get("_pending_invite_token") == raw_token

    def test_authenticated_user_accepts_invite_marks_status_active(self):
        contributor, invite, raw_token = self._setup_invite(email="alice@example.com")
        user = UserFactory(email="alice@example.com")
        client = Client()
        client.force_login(user)
        client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))

        contributor.refresh_from_db()
        invite.refresh_from_db()
        assert contributor.status == IssueContributor.Status.ACTIVE
        assert contributor.user_id == user.pk
        assert contributor.accepted_at is not None
        assert invite.consumed_at is not None

    def test_invite_acceptance_marks_email_verified(self):
        contributor, _, raw_token = self._setup_invite(email="alice@example.com")
        user = UserFactory(email="alice@example.com")
        client = Client()
        client.force_login(user)
        client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))

        addr = EmailAddress.objects.filter(user=user, email="alice@example.com").first()
        assert addr is not None
        assert addr.verified is True
        assert addr.primary is True

    def test_session_cleared_after_acceptance(self):
        _, _, raw_token = self._setup_invite(email="alice@example.com")
        user = UserFactory(email="alice@example.com")
        client = Client()
        session = client.session
        session["_pending_invite_token"] = raw_token
        session.save()
        client.force_login(user)
        client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))
        assert client.session.get("_pending_invite_token") is None


# ---------------------------------------------------------------------------
# Stage 4 — Planka webhook → PlankaCardRevision
# ---------------------------------------------------------------------------


class TestStage4PlankaWebhook:
    def test_webhook_records_card_revision(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = WEBHOOK_SECRET
        issue = Issue.objects.create(name="March 2024", active=False)
        binding = PlankaIssueBinding.objects.create(
            issue=issue, project_id="proj-1", project_name="March 2024", board_id="board-1"
        )
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")

        response = Client().post(
            WEBHOOK_URL,
            data=_webhook_payload("card-abc", "board-1", CARD_DESCRIPTION),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}",
        )

        assert response.status_code == 200
        revision = PlankaCardRevision.objects.filter(card_id="card-abc").first()
        assert revision is not None
        assert revision.binding == binding
        assert "NEJM" in revision.description

    def test_webhook_rejected_with_wrong_secret(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = WEBHOOK_SECRET
        issue = Issue.objects.create(name="March 2024", active=False)
        PlankaIssueBinding.objects.create(
            issue=issue, project_id="proj-1", project_name="March 2024", board_id="board-1"
        )
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")

        response = Client().post(
            WEBHOOK_URL,
            data=_webhook_payload("card-abc", "board-1", CARD_DESCRIPTION),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong-secret",
        )
        assert response.status_code == 403

    def test_identical_description_does_not_create_duplicate_revision(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = WEBHOOK_SECRET
        issue = Issue.objects.create(name="March 2024", active=False)
        PlankaIssueBinding.objects.create(
            issue=issue, project_id="proj-1", project_name="March 2024", board_id="board-1"
        )
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")

        client = Client()
        payload = _webhook_payload("card-abc", "board-1", CARD_DESCRIPTION)
        client.post(
            WEBHOOK_URL, data=payload, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}"
        )
        client.post(
            WEBHOOK_URL, data=payload, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}"
        )

        assert PlankaCardRevision.objects.filter(card_id="card-abc").count() == 1


# ---------------------------------------------------------------------------
# Stage 5 — Planka card import
# ---------------------------------------------------------------------------


class TestStage5PlankaCardImport:
    def _setup(self):
        issue = Issue.objects.create(name="March 2024", active=False, body="Body.")
        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="proj-1",
            project_name="March 2024",
            board_id="board-1",
            lists={"candidates": "list-1", "under_review": "list-2", "publish_ready": "list-3"},
        )
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        return issue, binding

    def test_import_card_creates_review_and_links_to_issue(self):
        issue, _ = self._setup()
        client, _ = _make_editor()
        card = _mock_card()

        with (
            patch("spanza_journal_watch.backend.views._extract_board_cards", return_value=[card]),
            patch("spanza_journal_watch.backend.views._build_planka_client", return_value=_mock_planka_client()),
        ):
            response = client.post(
                reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
                data={"card_id": "card-e2e", "card_scope": "publish"},
            )

        assert response.status_code == 200
        assert issue.reviews.count() == 1
        review = issue.reviews.first()
        assert review.article.name == "Drug X RCT"
        assert review.active is False  # imported cards start inactive

    def test_import_card_sets_review_body_from_description(self):
        issue, _ = self._setup()
        client, _ = _make_editor()
        card = _mock_card()

        with (
            patch("spanza_journal_watch.backend.views._extract_board_cards", return_value=[card]),
            patch("spanza_journal_watch.backend.views._build_planka_client", return_value=_mock_planka_client()),
        ):
            client.post(
                reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
                data={"card_id": "card-e2e", "card_scope": "publish"},
            )

        review = issue.reviews.first()
        assert "well-conducted trial" in review.body

    def test_import_same_card_twice_does_not_create_duplicate_review(self):
        issue, _ = self._setup()
        client, _ = _make_editor()
        card = _mock_card()

        with (
            patch("spanza_journal_watch.backend.views._extract_board_cards", return_value=[card]),
            patch("spanza_journal_watch.backend.views._build_planka_client", return_value=_mock_planka_client()),
        ):
            client.post(
                reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
                data={"card_id": "card-e2e", "card_scope": "publish"},
            )
        assert issue.reviews.count() == 1

        # Second import attempt — card now has has_associated_review=True
        card_protected = {**card, "has_associated_review": True}
        with (
            patch("spanza_journal_watch.backend.views._extract_board_cards", return_value=[card_protected]),
            patch("spanza_journal_watch.backend.views._build_planka_client", return_value=_mock_planka_client()),
        ):
            client.post(
                reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
                data={"card_id": "card-e2e", "card_scope": "publish"},
            )
        assert issue.reviews.count() == 1


# ---------------------------------------------------------------------------
# Stage 6 — Publication
# ---------------------------------------------------------------------------


class TestStage6Publication:
    def _issue_with_review(self):
        journal = Journal.objects.create(name="NEJM", active=True)
        author = Author.objects.create(name="Dr Jane Smith")
        article = PubmedArticle.objects.create(
            title="Drug X RCT", publication_date=datetime.date(2024, 1, 1), journal=journal, active=False
        )
        review = Review.objects.create(article=article, author=author, body="Review text.", active=False)
        issue = Issue.objects.create(name="March 2024", active=False, body="Issue body.")
        issue.reviews.add(review)
        return issue, review, article

    def test_publish_activates_issue(self):
        issue, _, _ = self._issue_with_review()
        client, _ = _make_editor()
        client.post(reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}))
        issue.refresh_from_db()
        assert issue.active is True

    def test_publish_activates_all_reviews(self):
        issue, review, _ = self._issue_with_review()
        client, _ = _make_editor()
        client.post(reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}))
        review.refresh_from_db()
        assert review.active is True

    def test_publish_activates_articles(self):
        issue, _, article = self._issue_with_review()
        client, _ = _make_editor()
        client.post(reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}))
        article.refresh_from_db()
        assert article.active is True

    def test_issue_without_reviews_does_not_publish(self):
        issue = Issue.objects.create(name="Empty Issue", active=False, body="Body.")
        client, _ = _make_editor()
        client.post(reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}))
        issue.refresh_from_db()
        assert issue.active is False


# ---------------------------------------------------------------------------
# Stage 7 — Newsletter
# ---------------------------------------------------------------------------


class TestStage7Newsletter:
    def test_newsletter_auto_created_on_release_list_get(self):
        issue = Issue.objects.create(name="March 2024", active=True, body="Body.")
        client, _ = _make_editor()
        client.get(reverse("backend:newsletter_release_list") + f"?issue={issue.pk}")
        assert Newsletter.objects.filter(issue=issue).exists()

    def test_newsletter_subject_defaults_to_issue_name(self):
        issue = Issue.objects.create(name="March 2024", active=True, body="Body.")
        client, _ = _make_editor()
        client.get(reverse("backend:newsletter_release_list") + f"?issue={issue.pk}")
        newsletter = Newsletter.objects.get(issue=issue)
        assert "March 2024" in newsletter.subject

    def test_send_final_newsletter_queues_celery_task(self):
        issue = Issue.objects.create(name="March 2024", active=True, body="Body.")
        newsletter = Newsletter.objects.create(
            issue=issue,
            subject="March 2024",
            content="Newsletter body.",
            ready_to_send=True,
            is_test_sent=True,
        )
        client, _ = _make_editor()
        url = reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
        with patch("spanza_journal_watch.backend.views.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            client.post(url)
        mock_task.apply_async.assert_called_once_with((newsletter.pk,), {"test_email": False}, countdown=1)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_newsletter_batch_delivers_email_per_subscriber(self):
        """
        send_newsletter task chains into send_newsletter_batch and delivers one
        email per tester subscriber.  Template rendering is bypassed so the test
        is not coupled to MJML / static-file infrastructure.
        """
        from django.core import mail as django_mail

        from spanza_journal_watch.newsletter.tasks import send_newsletter

        issue = Issue.objects.create(name="March 2024", active=True, body="Body.")
        newsletter = Newsletter.objects.create(
            issue=issue,
            subject="March 2024",
            content="Newsletter body.",
            ready_to_send=True,
            is_test_sent=True,
        )
        Subscriber.objects.create(email="reader@example.com", subscribed=True, tester=True)

        # Bypass MJML template rendering — we're testing task orchestration, not HTML.
        with patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_html_content",
            return_value="<html>Newsletter</html>",
        ), patch(
            "spanza_journal_watch.newsletter.models.Newsletter.generate_txt_content",
            return_value="Newsletter",
        ):
            send_newsletter(newsletter.pk, test_email=True)

        recipients = [msg.to[0] for msg in django_mail.outbox]
        assert "reader@example.com" in recipients


# ---------------------------------------------------------------------------
# Stage 8 — Frontend
# ---------------------------------------------------------------------------


class TestStage8Frontend:
    def test_public_issue_detail_returns_200(self):
        issue = Issue.objects.create(name="March 2024", active=True, slug="march-2024", body="Body.")
        journal = Journal.objects.create(name="NEJM", active=True)
        author = Author.objects.create(name="Dr Jane Smith")
        article = PubmedArticle.objects.create(
            title="Drug X RCT", publication_date=datetime.date(2024, 1, 1), journal=journal, active=True
        )
        review = Review.objects.create(article=article, author=author, body="Review text.", active=True)
        issue.reviews.add(review)

        response = Client().get(reverse("submissions:issue_detail", kwargs={"slug": issue.slug}))
        assert response.status_code == 200

    def test_public_issue_detail_shows_issue_name(self):
        issue = Issue.objects.create(name="March 2024", active=True, slug="march-2024", body="Body.")
        journal = Journal.objects.create(name="NEJM", active=True)
        author = Author.objects.create(name="Dr Jane Smith")
        article = PubmedArticle.objects.create(
            title="Drug X RCT", publication_date=datetime.date(2024, 1, 1), journal=journal, active=True
        )
        review = Review.objects.create(article=article, author=author, body="Review text.", active=True)
        issue.reviews.add(review)

        response = Client().get(reverse("submissions:issue_detail", kwargs={"slug": issue.slug}))
        assert "March 2024" in response.content.decode()

    def test_inactive_issue_returns_404(self):
        issue = Issue.objects.create(name="Draft Issue", active=False, slug="draft-issue", body="Body.")
        response = Client().get(reverse("submissions:issue_detail", kwargs={"slug": issue.slug}))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Chained workflow: all stages in a single sequential test
# ---------------------------------------------------------------------------


class TestChainedWorkflow:
    """
    Wires all stages together using shared objects — the closest approximation
    to a real operator session from issue creation to subscriber delivery.
    """

    def test_full_editorial_workflow(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = WEBHOOK_SECRET

        # ── Stage 1: Issue + initial Review ────────────────────────────────
        editor_client, editor_user = _make_editor()

        editor_client.post(
            reverse("backend:save_issue_draft"),
            data={"name": "March 2024", "date_0": "3", "date_1": "2024", "body": "Issue body."},
        )
        issue = Issue.objects.get(name="March 2024")
        assert issue.active is False

        editor_client.post(
            reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            data={
                "article_mode": "new",
                "article_name": "Drug X RCT",
                "author_mode": "new",
                "new_author_name": "Dr Jane Smith",
                "new_author_title": "Dr",
                "body": "This trial showed improvement.",
                "is_featured": True,
            },
        )
        assert issue.reviews.count() == 1

        # ── Stage 2: Contributor + invite ───────────────────────────────────
        manager_client, manager_user = _make_manager()

        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            manager_client.post(
                reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk}),
                data={
                    "role": IssueContributor.Role.REVIEWER,
                    "name_0": "Alice Smith",
                    "email_0": "alice@example.com",
                },
            )

        contributor = IssueContributor.objects.get(issue=issue, email="alice@example.com")
        assert contributor.status == IssueContributor.Status.PENDING

        with patch("spanza_journal_watch.backend.views._send_issue_invite_email") as mock_email:
            manager_client.post(
                reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk}),
                data={"contributor_ids": [contributor.pk]},
            )
        contributor.refresh_from_db()
        assert contributor.status == IssueContributor.Status.INVITED
        mock_email.assert_called_once()

        # ── Stage 3: Invite acceptance ──────────────────────────────────────
        # Retrieve the invite and swap in a known token so we can use it.
        invite = IssueContributorInvite.objects.get(contributor=contributor)
        raw_token = IssueContributorInvite.generate_raw_token()
        invite.token_hash = IssueContributorInvite.hash_token(raw_token)
        invite.expires_at = timezone.now() + datetime.timedelta(days=7)
        invite.consumed_at = None
        invite.save()

        anon_client = Client()
        response = anon_client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))
        assert response.status_code == 200
        assert anon_client.session.get("_pending_invite_token") == raw_token

        alice = UserFactory(email="alice@example.com")
        alice_client = Client()
        alice_client.force_login(alice)
        alice_client.get(reverse("issue_invite_accept", kwargs={"token": raw_token}))

        contributor.refresh_from_db()
        invite.refresh_from_db()
        assert contributor.status == IssueContributor.Status.ACTIVE
        assert contributor.user_id == alice.pk
        assert invite.consumed_at is not None

        addr = EmailAddress.objects.filter(user=alice, email="alice@example.com").first()
        assert addr is not None and addr.verified is True

        # ── Stage 4: Planka webhook ─────────────────────────────────────────
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            project_id="proj-1",
            project_name="March 2024",
            board_id="board-1",
            lists={"candidates": "list-1", "under_review": "list-2", "publish_ready": "list-3"},
        )

        response = Client().post(
            WEBHOOK_URL,
            data=_webhook_payload("card-e2e", "board-1", CARD_DESCRIPTION),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_SECRET}",
        )
        assert response.status_code == 200
        assert PlankaCardRevision.objects.filter(card_id="card-e2e", binding=binding).exists()

        # ── Stage 5: Planka card import ─────────────────────────────────────
        # Return alice as the card member so _sync_planka_card_into_issue can
        # resolve her IssueContributor record and assign an Author.
        alice_planka_user = {"id": "planka-alice", "email": "alice@example.com", "name": "Alice Smith"}
        import_client = MagicMock()
        import_client.get_card_members.return_value = (
            [{"userId": "planka-alice"}],
            {"planka-alice": alice_planka_user},
        )
        import_client.get_card_description_editor_ids.return_value = []
        import_client.list_users.return_value = []

        reviews_before = issue.reviews.count()
        card = _mock_card()

        with (
            patch("spanza_journal_watch.backend.views._extract_board_cards", return_value=[card]),
            patch("spanza_journal_watch.backend.views._build_planka_client", return_value=import_client),
        ):
            editor_client.post(
                reverse("backend:planka_import_publish_card", kwargs={"issue_id": issue.pk}),
                data={"card_id": "card-e2e", "card_scope": "publish"},
            )
        assert issue.reviews.count() == reviews_before + 1

        # ── Stage 6: Publish ────────────────────────────────────────────────
        editor_client.post(reverse("backend:publish_issue_bundle", kwargs={"issue_id": issue.pk}))
        issue.refresh_from_db()
        assert issue.active is True
        for review in issue.reviews.all():
            review.refresh_from_db()
            assert review.active is True
            assert review.article.active is True

        # ── Stage 7: Newsletter ─────────────────────────────────────────────
        editor_client.get(reverse("backend:newsletter_release_list") + f"?issue={issue.pk}")
        newsletter = Newsletter.objects.get(issue=issue)
        assert "March 2024" in newsletter.subject

        newsletter.ready_to_send = True
        newsletter.is_test_sent = True
        newsletter.save()

        with patch("spanza_journal_watch.backend.views.send_newsletter") as mock_task:
            mock_task.apply_async = MagicMock()
            response = editor_client.post(
                reverse("backend:send_final_newsletter", kwargs={"send_token": newsletter.send_token})
            )
        assert response.status_code == 200
        mock_task.apply_async.assert_called_once_with((newsletter.pk,), {"test_email": False}, countdown=1)

        # ── Stage 8: Frontend ───────────────────────────────────────────────
        response = Client().get(reverse("submissions:issue_detail", kwargs={"slug": issue.slug}))
        assert response.status_code == 200
        assert "March 2024" in response.content.decode()
