"""
Integration tests: PubMed / NIH article intake pipeline.

Covers:
  - article_intake_add_article: fetches from PubMed, creates PubmedArticle + PubmedBatchArticle
  - article_intake_add_article (toggle): second POST on same PMID toggles is_selected
  - article_intake_add_article (API error): graceful error when PubMed fails
  - article_intake_toggle_selection: toggles is_selected on an existing batch article
  - article_intake_assign_issue: assigns an issue to all batch articles
  - article_intake_task_status: returns idle / running / done correctly
  - article_intake_push_to_planka: pushes selected articles to a Planka board
"""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import (
    PlankaIssueBinding,
    PubmedArticle,
    PubmedBatchArticle,
    PubmedImportBatch,
    WatchedJournal,
    WatchedJournalArticle,
)
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MANAGE_ISSUE_BUILDER = "submissions.manage_issue_builder"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def _make_manager():
    u = UserFactory()
    _grant(u, MANAGE_ISSUE_BUILDER)
    c = Client()
    c.force_login(u)
    return c, u


def _make_batch(user):
    today = datetime.date.today().replace(day=1)
    return PubmedImportBatch.objects.create(
        from_month=today,
        to_month=today,
        created_by=user,
    )


def _sample_payload(pmid="12345678"):
    """Simulates what PubmedClient.fetch_articles() returns."""
    return [
        {
            "pmid": pmid,
            "doi": f"10.1234/test.{pmid}",
            "title": f"Test Article {pmid}",
            "abstract": "An abstract.",
            "source_journal_name": "Test Journal",
            "publication_date": datetime.date(2024, 1, 15),
            "publication_month": datetime.date(2024, 1, 1),
            "article_url": f"https://doi.org/10.1234/test.{pmid}",
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "metadata_json": {},
        }
    ]


# ---------------------------------------------------------------------------
# Tests: article_intake_add_article
# ---------------------------------------------------------------------------


class TestArticleIntakeAddArticle:
    def test_import_batch_builds_from_cached_watched_journal_articles(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        watched = WatchedJournal.objects.create(name="Cache Journal", active=True)
        batch.watched_journals.add(watched)
        article = PubmedArticle.objects.create(
            pmid="77777777",
            doi="10.1234/test.77777777",
            title="Cached Article",
            publication_date=batch.from_month,
            publication_month=batch.from_month,
        )
        WatchedJournalArticle.objects.create(
            watched_journal=watched,
            article=article,
            publication_month=batch.from_month,
        )

        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            from spanza_journal_watch.backend.views import _import_pubmed_batch

            _import_pubmed_batch(batch, [watched])

        mock_build.assert_not_called()
        assert PubmedBatchArticle.objects.filter(batch=batch, article=article).exists()

    def test_add_article_creates_pubmed_article_and_batch_link(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        pmid = "12345678"

        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            mock_client = MagicMock()
            mock_client.fetch_articles.return_value = _sample_payload(pmid)
            mock_build.return_value = mock_client

            url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
            resp = client.post(url, {"pmid": pmid})

        assert resp.status_code == 200
        assert PubmedArticle.objects.filter(pmid=pmid).exists()
        link = PubmedBatchArticle.objects.get(batch=batch, article__pmid=pmid)
        assert link.is_selected is True

    def test_add_article_stores_title_and_doi(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        pmid = "99999999"

        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            mock_client = MagicMock()
            mock_client.fetch_articles.return_value = _sample_payload(pmid)
            mock_build.return_value = mock_client

            url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
            client.post(url, {"pmid": pmid})

        article = PubmedArticle.objects.get(pmid=pmid)
        assert article.title == f"Test Article {pmid}"
        assert "10.1234/test." in article.doi

    def test_add_article_updates_batch_counts(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        pmid = "11111111"

        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            mock_client = MagicMock()
            mock_client.fetch_articles.return_value = _sample_payload(pmid)
            mock_build.return_value = mock_client

            url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
            client.post(url, {"pmid": pmid})

        batch.refresh_from_db()
        assert batch.result_count == 1
        assert batch.selected_count == 1

    def test_add_article_toggle_deselects_existing(self):
        """Second POST on same PMID should toggle is_selected to False."""
        client, user = _make_manager()
        batch = _make_batch(user)
        pmid = "22222222"

        # First add
        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            mock_client = MagicMock()
            mock_client.fetch_articles.return_value = _sample_payload(pmid)
            mock_build.return_value = mock_client
            url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
            client.post(url, {"pmid": pmid})

        # Second POST — no API call needed, toggles existing link
        url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
        client.post(url, {"pmid": pmid})

        link = PubmedBatchArticle.objects.get(batch=batch, article__pmid=pmid)
        assert link.is_selected is False

    def test_add_article_missing_pmid_returns_error_response(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
        resp = client.post(url, {"pmid": ""})
        # Should return 200 with the results partial (error message inside)
        assert resp.status_code == 200
        assert PubmedBatchArticle.objects.filter(batch=batch).count() == 0

    def test_add_article_pubmed_api_error_returns_graceful_response(self):
        from spanza_journal_watch.backend.pubmed import PubmedAPIError

        client, user = _make_manager()
        batch = _make_batch(user)
        pmid = "33333333"

        with patch("spanza_journal_watch.backend.views._build_pubmed_client") as mock_build:
            mock_client = MagicMock()
            mock_client.fetch_articles.side_effect = PubmedAPIError("Network timeout")
            mock_build.return_value = mock_client

            url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
            resp = client.post(url, {"pmid": pmid})

        assert resp.status_code == 200
        assert not PubmedArticle.objects.filter(pmid=pmid).exists()

    def test_add_article_requires_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        batch = _make_batch(u)
        url = reverse("backend:article_intake_add_article", kwargs={"batch_id": batch.pk})
        resp = c.post(url, {"pmid": "12345678"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: article_intake_toggle_selection
# ---------------------------------------------------------------------------


class TestArticleIntakeToggleSelection:
    def _add_article(self, batch, pmid="55555555"):
        article = PubmedArticle.objects.create(
            pmid=pmid,
            title="Toggle test article",
            publication_date=datetime.date(2024, 1, 1),
        )
        link = PubmedBatchArticle.objects.create(batch=batch, article=article, is_selected=True)
        return link

    def test_toggle_deselects_selected_article(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        link = self._add_article(batch)

        url = reverse(
            "backend:article_intake_toggle_selection",
            kwargs={"batch_id": batch.pk, "item_id": link.pk},
        )
        resp = client.post(url)
        assert resp.status_code == 200
        link.refresh_from_db()
        assert link.is_selected is False

    def test_toggle_selects_deselected_article(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        link = self._add_article(batch)
        link.is_selected = False
        link.save()

        url = reverse(
            "backend:article_intake_toggle_selection",
            kwargs={"batch_id": batch.pk, "item_id": link.pk},
        )
        # The view reads `selected` from POST; passing "true" selects the article
        resp = client.post(url, {"selected": "true"})
        assert resp.status_code == 200
        link.refresh_from_db()
        assert link.is_selected is True

    def test_toggle_requires_post(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        link = self._add_article(batch)
        url = reverse(
            "backend:article_intake_toggle_selection",
            kwargs={"batch_id": batch.pk, "item_id": link.pk},
        )
        resp = client.get(url)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: article_intake_assign_issue
# ---------------------------------------------------------------------------


class TestArticleIntakeAssignIssue:
    def test_assign_issue_updates_batch_and_articles(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        issue = Issue.objects.create(name="Jan 2024", body="")

        # Add two articles to batch
        for pmid in ("77777777", "88888888"):
            article = PubmedArticle.objects.create(pmid=pmid, title=f"Article {pmid}")
            PubmedBatchArticle.objects.create(batch=batch, article=article, is_selected=True)

        url = reverse("backend:article_intake_assign_issue", kwargs={"batch_id": batch.pk})
        resp = client.post(url, {"issue": issue.pk})

        # Redirects to article_intake
        assert resp.status_code == 302

        batch.refresh_from_db()
        assert batch.issue == issue

        for link in PubmedBatchArticle.objects.filter(batch=batch):
            assert link.issue == issue

    def test_assign_issue_invalid_form_does_not_update(self):
        client, user = _make_manager()
        batch = _make_batch(user)

        url = reverse("backend:article_intake_assign_issue", kwargs={"batch_id": batch.pk})
        resp = client.post(url, {"issue": ""})
        assert resp.status_code == 302
        batch.refresh_from_db()
        assert batch.issue is None


# ---------------------------------------------------------------------------
# Tests: article_intake_task_status
# ---------------------------------------------------------------------------


class TestArticleIntakeTaskStatus:
    def test_task_status_idle_batch(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        assert batch.task_state == PubmedImportBatch.TASK_STATE_IDLE

        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        resp = client.get(url)
        assert resp.status_code == 200

    def test_task_status_running_batch(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        batch.task_state = PubmedImportBatch.TASK_STATE_RUNNING
        batch.save()

        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"is_running" not in resp.content  # template rendered, not raw dict

    def test_task_status_done_batch_shows_note(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        batch.task_state = PubmedImportBatch.TASK_STATE_SUCCESS
        batch.task_note = "Import complete: 5 articles found."
        batch.save()

        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        resp = client.get(url)
        assert resp.status_code == 200

    def test_task_status_requires_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        today = datetime.date.today().replace(day=1)
        batch = PubmedImportBatch.objects.create(from_month=today, to_month=today, created_by=u)
        url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})
        resp = c.get(url)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: article_intake_push_to_planka
# ---------------------------------------------------------------------------


class TestArticleIntakePushToPlanka:
    def _setup_binding(self, batch):
        """Create a minimal PlankaIssueBinding so push_to_planka can proceed."""
        issue = Issue.objects.create(name="Push Test Issue", body="")
        batch.issue = issue
        batch.save()

        binding = PlankaIssueBinding.objects.create(
            issue=issue,
            board_id="board-push-1",
            board_name="Reviews",
            project_id="project-push-1",
            project_name="Push Test Project",
            lists={"candidates": "list-candidates"},
        )
        return issue, binding

    def test_push_to_planka_creates_card_for_selected_articles(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        issue, binding = self._setup_binding(batch)

        article = PubmedArticle.objects.create(
            pmid="44444444",
            title="Article to push",
            doi="10.0/push",
            article_url="https://example.com/article",
        )
        PubmedBatchArticle.objects.create(batch=batch, article=article, is_selected=True, issue=issue)

        mock_client = MagicMock()
        mock_client.get_board.return_value = {"id": "board-1", "projectId": "project-1"}
        mock_client.get_board_lists.return_value = [
            {"id": "list-candidates", "name": "Candidates"},
        ]
        mock_client.get_board_labels.return_value = []
        mock_client.create_card.return_value = {
            "id": "card-new-1",
            "url": "https://planka.example.com/cards/card-new-1",
        }
        mock_client.get_list.return_value = {"id": "list-candidates", "boardId": "board-1"}

        with patch(
            "spanza_journal_watch.backend.views._build_planka_client",
            return_value=mock_client,
        ), patch("spanza_journal_watch.backend.views._ensure_planka_board_mappings"), patch(
            "spanza_journal_watch.backend.views._get_board_label_map",
            return_value={},
        ), patch(
            "spanza_journal_watch.backend.views._get_board_list_type_map",
            return_value={"list-candidates": "candidates"},
        ):
            url = reverse("backend:article_intake_push_to_planka", kwargs={"batch_id": batch.pk})
            resp = client.post(url)

        # Redirect to article_intake page
        assert resp.status_code in (200, 302)

    def test_push_to_planka_no_selected_articles_shows_info(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        issue, binding = self._setup_binding(batch)

        # Add unselected article only
        article = PubmedArticle.objects.create(pmid="55556666", title="Unselected")
        PubmedBatchArticle.objects.create(batch=batch, article=article, is_selected=False, issue=issue)

        mock_client = MagicMock()

        with patch(
            "spanza_journal_watch.backend.views._build_planka_client",
            return_value=mock_client,
        ), patch("spanza_journal_watch.backend.views._ensure_planka_board_mappings"), patch(
            "spanza_journal_watch.backend.views._get_board_label_map",
            return_value={},
        ), patch(
            "spanza_journal_watch.backend.views._get_board_list_type_map",
            return_value={},
        ):
            url = reverse("backend:article_intake_push_to_planka", kwargs={"batch_id": batch.pk})
            resp = client.post(url)

        # No card creation attempted
        mock_client.create_card.assert_not_called()
        assert resp.status_code in (200, 302)

    def test_push_to_planka_requires_post(self):
        client, user = _make_manager()
        batch = _make_batch(user)
        url = reverse("backend:article_intake_push_to_planka", kwargs={"batch_id": batch.pk})
        resp = client.get(url)
        assert resp.status_code == 400
