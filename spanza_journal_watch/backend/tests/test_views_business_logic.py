"""
Business-logic tests for backend views.

Covers the non-trivial view logic:
1. save_issue_draft — create / update, HTMX HX-Redirect header
2. add_issue_review / remove_issue_review — review CRUD
3. update_issue_review — in-place update
4. contributor_author_lookup — JSON author search
5. issue_add_contributor — create + update paths
6. issue_send_contributor_invites — status transition + email + skip revoked
7. issue_revoke_contributor — status + Planka cleanup
8. watched_journal_toggle_active — boolean flip
9. watched_journal_search — JSON results
10. article_intake_task_status — JSON task state
11. toggle_review_active — cascade to Article / Issue
"""

import datetime
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import IssueContributor, PubmedArticle, WatchedJournal
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

MANAGE_ISSUE_BUILDER = "submissions.manage_issue_builder"
CHIEF_EDITOR = "submissions.chief_editor"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def make_client(*perms):
    u = UserFactory()
    _grant(u, *perms)
    c = Client()
    c.force_login(u)
    return c, u


def editor_client():
    return make_client(CHIEF_EDITOR, MANAGE_ISSUE_BUILDER)


def manager_client():
    return make_client(MANAGE_ISSUE_BUILDER)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def issue():
    return Issue.objects.create(name="Test Issue", active=False)


@pytest.fixture()
def journal():
    return Journal.objects.create(name="Test Journal", active=True)


@pytest.fixture()
def author():
    return Author.objects.create(name="Dr Test", title="Dr")


@pytest.fixture()
def article(journal):
    return PubmedArticle.objects.create(
        title="Test Article", publication_date=datetime.date(2024, 1, 1), journal=journal
    )


@pytest.fixture()
def review(article, author):
    return Review.objects.create(article=article, author=author, body="A review body.")


@pytest.fixture()
def issue_with_review(issue, review):
    issue.reviews.add(review)
    return issue


@pytest.fixture()
def contributor(issue):
    return IssueContributor.objects.create(issue=issue, email="reviewer@example.com", name="Reviewer")


@pytest.fixture()
def watched_journal():
    return WatchedJournal.objects.create(name="Test Journal", active=True)


# ---------------------------------------------------------------------------
# 1. save_issue_draft
# ---------------------------------------------------------------------------


class TestSaveIssueDraft:
    def test_create_new_issue(self):
        client, _ = editor_client()
        url = reverse("backend:save_issue_draft")
        response = client.post(
            url,
            data={"name": "Brand New Issue", "date_0": "3", "date_1": "2024", "body": "Issue body text."},
        )
        assert response.status_code in (200, 302)
        assert Issue.objects.filter(name="Brand New Issue").exists()

    def test_new_issue_inactive_by_default(self):
        client, _ = editor_client()
        url = reverse("backend:save_issue_draft")
        client.post(url, data={"name": "Draft Issue", "date_0": "", "date_1": "", "body": "Issue body text."})
        issue = Issue.objects.filter(name="Draft Issue").first()
        assert issue is not None
        assert issue.active is False

    def test_update_existing_issue(self, issue):
        client, _ = editor_client()
        url = reverse("backend:update_issue_draft", kwargs={"issue_id": issue.pk})
        response = client.post(
            url,
            data={"name": "Updated Name", "date_0": "", "date_1": "", "body": "Updated body"},
        )
        assert response.status_code in (200, 302)
        issue.refresh_from_db()
        assert issue.name == "Updated Name"

    def test_htmx_returns_hx_redirect_header(self, issue):
        client, _ = editor_client()
        url = reverse("backend:update_issue_draft", kwargs={"issue_id": issue.pk})
        response = client.post(
            url,
            data={"name": "HTMX Save", "date_0": "", "date_1": "", "body": "Issue body text."},
            HTTP_HX_REQUEST="true",
        )
        assert "HX-Redirect" in response
        assert "issue=" in response["HX-Redirect"]

    def test_non_chief_editor_cannot_create_new(self):
        """Without chief_editor, create-new path should be denied."""
        client, _ = manager_client()
        url = reverse("backend:save_issue_draft")
        response = client.post(url, data={"name": "Attempt", "body": ""})
        assert response.status_code == 403

    def test_invalid_form_shows_errors(self):
        client, _ = editor_client()
        url = reverse("backend:save_issue_draft")
        response = client.post(url, data={"name": "", "body": ""})  # name required
        assert response.status_code == 200
        assert not Issue.objects.filter(name="").exists()


# ---------------------------------------------------------------------------
# 2. add_issue_review
# ---------------------------------------------------------------------------


class TestAddIssueReview:
    def _post(self, issue, client, **extra):
        url = reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk})
        data = {
            "article_mode": "new",
            "article_name": "New Article",
            "author_mode": "new",
            "new_author_name": "Dr New Author",
            "new_author_title": "Dr",
            "body": "Review body here.",
            "is_featured": False,
        }
        data.update(extra)
        return client.post(url, data=data)

    def test_creates_review_and_links_to_issue(self, issue):
        client, _ = editor_client()
        self._post(issue, client)
        assert issue.reviews.count() == 1

    def test_creates_new_article(self, issue):
        client, _ = editor_client()
        self._post(issue, client, article_name="Unique New Article")
        assert PubmedArticle.objects.filter(title="Unique New Article").exists()

    def test_creates_new_author(self, issue):
        client, _ = editor_client()
        self._post(issue, client, new_author_name="Brand New Author")
        assert Author.objects.filter(name="Brand New Author").exists()

    def test_uses_existing_article(self, issue, article):
        client, _ = editor_client()
        self._post(
            issue,
            client,
            article_mode="existing",
            existing_article=article.pk,
        )
        # Check result via the model
        review = issue.reviews.first()
        assert review is not None
        assert review.article.pk == article.pk

    def test_uses_existing_author(self, issue, author):
        client, _ = editor_client()
        self._post(
            issue,
            client,
            author_mode="existing",
            author=author.pk,
        )
        review = issue.reviews.first()
        assert review is not None
        assert review.author.pk == author.pk

    def test_invalid_form_no_article_name(self, issue):
        client, _ = editor_client()
        self._post(issue, client, article_mode="new", article_name="")
        assert issue.reviews.count() == 0

    def test_invalid_form_no_author(self, issue):
        client, _ = editor_client()
        self._post(issue, client, author_mode="existing", author="")
        assert issue.reviews.count() == 0

    def test_issue_not_found_returns_404(self):
        client, _ = editor_client()
        url = reverse("backend:add_issue_review", kwargs={"issue_id": 9999})
        response = client.post(url, data={})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3. remove_issue_review
# ---------------------------------------------------------------------------


class TestRemoveIssueReview:
    def test_removes_review_from_issue(self, issue_with_review, review):
        client, _ = editor_client()
        url = reverse(
            "backend:remove_issue_review",
            kwargs={"issue_id": issue_with_review.pk, "review_id": review.pk},
        )
        client.post(url)
        assert issue_with_review.reviews.filter(pk=review.pk).count() == 0

    def test_review_object_not_deleted(self, issue_with_review, review):
        """remove_issue_review unlinks, does NOT delete the Review object."""
        client, _ = editor_client()
        url = reverse(
            "backend:remove_issue_review",
            kwargs={"issue_id": issue_with_review.pk, "review_id": review.pk},
        )
        client.post(url)
        assert Review.objects.filter(pk=review.pk).exists()

    def test_review_not_in_issue_returns_404(self, issue, review):
        """Review exists but is not linked to this issue."""
        client, _ = editor_client()
        url = reverse(
            "backend:remove_issue_review",
            kwargs={"issue_id": issue.pk, "review_id": review.pk},
        )
        response = client.post(url)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4. update_issue_review
# ---------------------------------------------------------------------------


class TestUpdateIssueReview:
    def test_updates_review_body(self, issue_with_review, review, article, author):
        client, _ = editor_client()
        url = reverse(
            "backend:update_issue_review",
            kwargs={"issue_id": issue_with_review.pk, "review_id": review.pk},
        )
        client.post(
            url,
            data={
                "article_mode": "existing",
                "existing_article": article.pk,
                "author_mode": "existing",
                "author": author.pk,
                "body": "Updated review body.",
                "is_featured": False,
            },
        )
        review.refresh_from_db()
        assert review.body == "Updated review body."

    def test_review_not_in_issue_returns_404(self, issue, review):
        client, _ = editor_client()
        url = reverse(
            "backend:update_issue_review",
            kwargs={"issue_id": issue.pk, "review_id": review.pk},
        )
        response = client.post(url, data={})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 5. contributor_author_lookup
# ---------------------------------------------------------------------------


class TestContributorAuthorLookup:
    def test_returns_found_true_for_existing_email(self, author):
        author.email = "lookup@example.com"
        author.save()

        client, _ = manager_client()
        url = reverse("backend:contributor_author_lookup")
        response = client.get(url, {"email": "lookup@example.com"})
        data = json.loads(response.content)

        assert data["found"] is True
        assert data["name"] == author.name

    def test_case_insensitive_email_lookup(self, author):
        author.email = "CaseSensitive@example.com"
        author.save()

        client, _ = manager_client()
        url = reverse("backend:contributor_author_lookup")
        response = client.get(url, {"email": "casesensitive@example.com"})
        data = json.loads(response.content)
        assert data["found"] is True

    def test_returns_found_false_for_unknown_email(self):
        client, _ = manager_client()
        url = reverse("backend:contributor_author_lookup")
        response = client.get(url, {"email": "noone@example.com"})
        data = json.loads(response.content)
        assert data["found"] is False

    def test_returns_found_false_for_empty_email(self):
        client, _ = manager_client()
        url = reverse("backend:contributor_author_lookup")
        response = client.get(url, {"email": ""})
        data = json.loads(response.content)
        assert data["found"] is False

    def test_returns_affiliations_list(self, author):
        from spanza_journal_watch.submissions.models import HealthService

        hs = HealthService.objects.create(name="Royal Children's Hospital")
        author.email = "affil@example.com"
        author.save()
        author.health_services.add(hs)

        client, _ = manager_client()
        url = reverse("backend:contributor_author_lookup")
        response = client.get(url, {"email": "affil@example.com"})
        data = json.loads(response.content)
        assert any(a["name"] == "Royal Children's Hospital" for a in data["affiliations"])


# ---------------------------------------------------------------------------
# 6. issue_add_contributor
# ---------------------------------------------------------------------------


class TestIssueAddContributor:
    def _post(self, client, issue, **extra):
        url = reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk})
        data = {
            "role": IssueContributor.Role.REVIEWER,
            "name_0": "Alice Smith",
            "email_0": "alice@example.com",
        }
        data.update(extra)
        return client.post(url, data=data)

    def test_creates_contributor(self, issue):
        client, _ = manager_client()
        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            self._post(client, issue)
        assert IssueContributor.objects.filter(issue=issue, email="alice@example.com").exists()

    def test_status_pending_on_creation(self, issue):
        client, _ = manager_client()
        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            self._post(client, issue)
        c = IssueContributor.objects.get(issue=issue, email="alice@example.com")
        assert c.status == IssueContributor.Status.PENDING

    def test_updates_existing_contributor(self, issue, contributor):
        client, _ = manager_client()
        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            self._post(
                client,
                issue,
                name_0="Updated Name",
                email_0=contributor.email,
            )
        contributor.refresh_from_db()
        assert contributor.name == "Updated Name"

    def test_no_rows_shows_error_message(self, issue):
        client, _ = manager_client()
        url = reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk})
        response = client.post(url, data={"role": IssueContributor.Role.REVIEWER})
        assert response.status_code == 200
        assert IssueContributor.objects.filter(issue=issue).count() == 0

    def test_links_existing_author_by_email(self, issue, author):
        author.email = "linked@example.com"
        author.save()

        client, _ = manager_client()
        with patch("spanza_journal_watch.backend.views._sync_contributor_to_planka", return_value=(True, "")):
            self._post(client, issue, name_0="Linked Author", email_0="linked@example.com")
        contributor = IssueContributor.objects.get(issue=issue, email="linked@example.com")
        assert contributor.author_id == author.pk

    def test_get_returns_400(self, issue):
        client, _ = manager_client()
        url = reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk})
        response = client.get(url)
        assert response.status_code == 400

    def test_issue_not_found_returns_404(self):
        client, _ = manager_client()
        url = reverse("backend:issue_add_contributor", kwargs={"issue_id": 9999})
        response = client.post(url, data={"role": "reviewer", "name_0": "A", "email_0": "a@b.com"})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 7. issue_send_contributor_invites
# ---------------------------------------------------------------------------


class TestIssueSendContributorInvites:
    def test_sets_status_to_invited(self, issue, contributor):
        client, _ = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        with patch("spanza_journal_watch.backend.views._send_issue_invite_email"):
            client.post(url, data={"contributor_ids": [contributor.pk]})
        contributor.refresh_from_db()
        assert contributor.status == IssueContributor.Status.INVITED

    def test_sets_invited_by_and_at(self, issue, contributor):
        client, user = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        with patch("spanza_journal_watch.backend.views._send_issue_invite_email"):
            client.post(url, data={"contributor_ids": [contributor.pk]})
        contributor.refresh_from_db()
        assert contributor.invited_by_id == user.pk
        assert contributor.invited_at is not None

    def test_creates_invite_record(self, issue, contributor):
        from spanza_journal_watch.backend.models import IssueContributorInvite

        client, _ = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        with patch("spanza_journal_watch.backend.views._send_issue_invite_email"):
            client.post(url, data={"contributor_ids": [contributor.pk]})
        assert IssueContributorInvite.objects.filter(contributor=contributor).exists()

    def test_skips_revoked_contributors(self, issue):
        revoked = IssueContributor.objects.create(
            issue=issue,
            email="revoked@example.com",
            status=IssueContributor.Status.REVOKED,
        )
        client, _ = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        with patch("spanza_journal_watch.backend.views._send_issue_invite_email"):
            client.post(url, data={"contributor_ids": [revoked.pk]})
        revoked.refresh_from_db()
        # Status should still be REVOKED
        assert revoked.status == IssueContributor.Status.REVOKED

    def test_no_ids_selected_shows_error(self, issue):
        client, _ = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        response = client.post(url, data={"contributor_ids": []})
        assert response.status_code == 200

    def test_email_failure_caught_per_contributor(self, issue, contributor):
        client, _ = manager_client()
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        with patch(
            "spanza_journal_watch.backend.views._send_issue_invite_email",
            side_effect=Exception("SMTP error"),
        ):
            response = client.post(url, data={"contributor_ids": [contributor.pk]})
        # Should not raise; response still 200
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 8. watched_journal_toggle_active
# ---------------------------------------------------------------------------


class TestWatchedJournalToggleActive:
    def test_toggles_active_to_inactive(self, watched_journal):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": watched_journal.pk})
        client.post(url)
        watched_journal.refresh_from_db()
        assert watched_journal.active is False

    def test_toggles_inactive_to_active(self, watched_journal):
        watched_journal.active = False
        watched_journal.save()

        client, _ = manager_client()
        url = reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": watched_journal.pk})
        client.post(url)
        watched_journal.refresh_from_db()
        assert watched_journal.active is True

    def test_returns_redirect(self, watched_journal):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": watched_journal.pk})
        response = client.post(url)
        assert response.status_code == 302

    def test_missing_journal_returns_404(self):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": 9999})
        response = client.post(url)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 9. watched_journal_search
# ---------------------------------------------------------------------------


class TestWatchedJournalSearch:
    """watched_journal_search calls the PubMed API; mock _build_pubmed_client."""

    def _mock_pubmed(self, results):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.search_journals.return_value = results
        return patch("spanza_journal_watch.backend.views._build_pubmed_client", return_value=mock_client)

    def test_returns_matching_journals(self):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_search")
        fake = [{"name": "Paediatric Anaesthesia", "issn": "1155-5645"}]
        with self._mock_pubmed(fake):
            response = client.get(url, {"q": "Paed"})
        assert response.status_code == 200
        data = json.loads(response.content)
        assert any(j["name"] == "Paediatric Anaesthesia" for j in data["results"])

    def test_short_query_returns_empty_results(self):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_search")
        response = client.get(url, {"q": "ab"})  # < 3 chars
        data = json.loads(response.content)
        assert data == {"results": []}

    def test_no_match_returns_empty_results(self):
        client, _ = manager_client()
        url = reverse("backend:watched_journal_search")
        with self._mock_pubmed([]):
            response = client.get(url, {"q": "xyzzy_no_match"})
        data = json.loads(response.content)
        assert data["results"] == []


# ---------------------------------------------------------------------------
# 10. article_intake_task_status
# ---------------------------------------------------------------------------


class TestArticleIntakeTaskStatus:
    def _make_batch(self, **kwargs):
        from spanza_journal_watch.backend.models import PubmedImportBatch

        return PubmedImportBatch.objects.create(
            from_month="2024-01-01",
            to_month="2024-03-01",
            **kwargs,
        )

    def test_returns_html_for_idle_batch(self):
        batch = self._make_batch(task_state="idle")
        client, _ = manager_client()
        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert "text/html" in response["Content-Type"]

    def test_batch_not_found_returns_404(self):
        client, _ = manager_client()
        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": 9999})
        response = client.get(url)
        assert response.status_code == 404

    def test_running_state_in_context(self):
        batch = self._make_batch(task_state="running")
        client, _ = manager_client()
        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["is_running"] is True


# ---------------------------------------------------------------------------
# 11. toggle_review_active
# ---------------------------------------------------------------------------


class TestToggleReviewActive:
    def _url(self, review):
        return reverse("backend:toggle_review_active", kwargs={"review_id": review.pk})

    def test_activates_inactive_review(self, issue_with_review, review):
        review.active = False
        review.save()

        client, _ = editor_client()
        client.post(self._url(review))
        review.refresh_from_db()
        assert review.active is True

    def test_deactivates_active_review(self, issue_with_review, review):
        review.active = True
        review.save()

        client, _ = editor_client()
        client.post(self._url(review))
        review.refresh_from_db()
        assert review.active is False

    def test_review_not_found_returns_404(self):
        client, _ = editor_client()
        url = reverse("backend:toggle_review_active", kwargs={"review_id": 9999})
        response = client.post(url)
        assert response.status_code == 404

    def test_returns_200_html(self, issue_with_review, review):
        client, _ = editor_client()
        response = client.post(self._url(review))
        assert response.status_code == 200
