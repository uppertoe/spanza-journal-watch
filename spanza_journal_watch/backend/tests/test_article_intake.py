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

        with (
            patch(
                "spanza_journal_watch.backend.views._build_planka_client",
                return_value=mock_client,
            ),
            patch("spanza_journal_watch.backend.views._ensure_planka_board_mappings"),
            patch(
                "spanza_journal_watch.backend.views._get_board_label_map",
                return_value={},
            ),
            patch(
                "spanza_journal_watch.backend.views._get_board_list_type_map",
                return_value={"list-candidates": "candidates"},
            ),
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

        with (
            patch(
                "spanza_journal_watch.backend.views._build_planka_client",
                return_value=mock_client,
            ),
            patch("spanza_journal_watch.backend.views._ensure_planka_board_mappings"),
            patch(
                "spanza_journal_watch.backend.views._get_board_label_map",
                return_value={},
            ),
            patch(
                "spanza_journal_watch.backend.views._get_board_list_type_map",
                return_value={},
            ),
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


# ---------------------------------------------------------------------------
# Tests: check-for-new flow ("new since your last look")
# ---------------------------------------------------------------------------


def _make_batch_with_watched(user):
    today = datetime.date.today().replace(day=1)
    batch = PubmedImportBatch.objects.create(from_month=today, to_month=today, created_by=user)
    watched = WatchedJournal.objects.create(name="WJ Check", active=True)
    batch.watched_journals.add(watched)
    return batch, watched


class TestArticleIntakeCheckForNew:
    def test_check_for_new_queues_task_when_gate_open(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        with patch("spanza_journal_watch.backend.views.check_batch_for_new_articles_task.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="task-1")
            resp = client.post(reverse("backend:article_intake_check_for_new", kwargs={"batch_id": batch.pk}))
        assert resp.status_code in (200, 302)
        mock_delay.assert_called_once_with(batch.pk)
        # user_view created lazily on results render; here we just ensure no error.
        _ = PubmedBatchUserView

    def test_check_for_new_blocked_when_recently_fetched(self):
        from django.utils import timezone

        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        batch.last_pubmed_fetched_at = timezone.now()
        batch.save(update_fields=["last_pubmed_fetched_at", "modified"])

        with patch("spanza_journal_watch.backend.views.check_batch_for_new_articles_task.delay") as mock_delay:
            resp = client.post(reverse("backend:article_intake_check_for_new", kwargs={"batch_id": batch.pk}))
        assert resp.status_code in (200, 302)
        mock_delay.assert_not_called()

    def test_check_for_new_allowed_after_window_expires(self):
        from django.utils import timezone

        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        batch.last_pubmed_fetched_at = timezone.now() - datetime.timedelta(minutes=20)
        batch.save(update_fields=["last_pubmed_fetched_at", "modified"])

        with patch("spanza_journal_watch.backend.views.check_batch_for_new_articles_task.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="task-2")
            client.post(reverse("backend:article_intake_check_for_new", kwargs={"batch_id": batch.pk}))
        mock_delay.assert_called_once_with(batch.pk)


class TestArticleIntakeUserView:
    def test_results_create_user_view_with_now_baseline(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)

        resp = client.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))
        assert resp.status_code == 200
        assert PubmedBatchUserView.objects.filter(batch=batch, user=user).exists()

    def test_per_user_baselines_are_isolated(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client_a, user_a = _make_manager()
        client_b, user_b = _make_manager()
        batch, _ = _make_batch_with_watched(user_a)

        client_a.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))
        client_b.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))

        views = {v.user_id: v for v in PubmedBatchUserView.objects.filter(batch=batch)}
        assert user_a.pk in views and user_b.pk in views
        # Independent rows.
        assert views[user_a.pk].pk != views[user_b.pk].pk

    def test_mark_all_seen_advances_baseline_and_clears_seen_ids(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client, user = _make_manager()
        batch, watched = _make_batch_with_watched(user)
        # Trigger user_view creation, then plant a stale baseline + a per-row ack.
        client.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))
        view = PubmedBatchUserView.objects.get(batch=batch, user=user)
        old_baseline = view.last_seen_at - datetime.timedelta(days=1)
        view.last_seen_at = old_baseline
        view.seen_batch_article_ids = [999]
        view.save(update_fields=["last_seen_at", "seen_batch_article_ids", "modified"])

        resp = client.post(reverse("backend:article_intake_mark_all_seen", kwargs={"batch_id": batch.pk}))
        assert resp.status_code in (200, 302)

        view.refresh_from_db()
        assert view.last_seen_at > old_baseline
        assert view.seen_batch_article_ids == []

    def test_mark_row_seen_appends_row_id(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client, user = _make_manager()
        batch, watched = _make_batch_with_watched(user)
        article = PubmedArticle.objects.create(pmid="11112222", title="A")
        row = PubmedBatchArticle.objects.create(batch=batch, article=article, watched_journal=watched)

        # Create the user_view first.
        client.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}))

        resp = client.post(
            reverse("backend:article_intake_mark_row_seen", kwargs={"batch_id": batch.pk, "item_id": row.pk})
        )
        assert resp.status_code == 204
        view = PubmedBatchUserView.objects.get(batch=batch, user=user)
        assert row.pk in (view.seen_batch_article_ids or [])

    def test_toggle_selection_implicitly_acknowledges_row(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView

        client, user = _make_manager()
        batch, watched = _make_batch_with_watched(user)
        article = PubmedArticle.objects.create(pmid="33334444", title="B")
        row = PubmedBatchArticle.objects.create(batch=batch, article=article, watched_journal=watched)

        client.post(
            reverse("backend:article_intake_toggle_selection", kwargs={"batch_id": batch.pk, "item_id": row.pk}),
            data={"selected": "1"},
        )
        view = PubmedBatchUserView.objects.get(batch=batch, user=user)
        assert row.pk in (view.seen_batch_article_ids or [])

    def test_is_new_flag_set_on_rows_created_after_baseline(self):
        from spanza_journal_watch.backend.models import PubmedBatchUserView
        from spanza_journal_watch.backend.views import _article_intake_results_context

        client, user = _make_manager()
        batch, watched = _make_batch_with_watched(user)

        # Seed user_view with an early baseline.
        view = PubmedBatchUserView.objects.create(
            batch=batch, user=user, last_seen_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        )

        article = PubmedArticle.objects.create(pmid="55556666", title="C")
        row = PubmedBatchArticle.objects.create(batch=batch, article=article, watched_journal=watched)

        # The row has no MeSH terms, so switch off the default paediatric filter.
        ctx = _article_intake_results_context(batch, {"paediatric_only": "0"}, user=user)
        result_row = next(r for r in ctx["result_rows"] if r.pk == row.pk)
        assert result_row.is_new is True
        assert ctx["new_count"] == 1

        # Acknowledge the row → no longer flagged new.
        view.seen_batch_article_ids = [row.pk]
        view.save(update_fields=["seen_batch_article_ids", "modified"])
        ctx2 = _article_intake_results_context(batch, {"paediatric_only": "0"}, user=user)
        result_row2 = next(r for r in ctx2["result_rows"] if r.pk == row.pk)
        assert result_row2.is_new is False
        assert ctx2["new_count"] == 0


# ---------------------------------------------------------------------------
# Tests: batch regeneration carry-forward (Planka push state + staging state)
# ---------------------------------------------------------------------------


class TestBatchRegenerationCarryForward:
    def _make_issue_batch(self, user, issue, watched=None):
        today = datetime.date.today().replace(day=1)
        batch = PubmedImportBatch.objects.create(from_month=today, to_month=today, created_by=user, issue=issue)
        if watched is None:
            watched = WatchedJournal.objects.create(name="WJ Carry", active=True)
        batch.watched_journals.add(watched)
        return batch, watched

    def _make_cached_article(self, watched, pmid, publication_month):
        article = PubmedArticle.objects.create(
            pmid=pmid,
            title=f"Article {pmid}",
            publication_date=publication_month,
            publication_month=publication_month,
        )
        WatchedJournalArticle.objects.create(
            watched_journal=watched,
            article=article,
            publication_month=publication_month,
        )
        return article

    def test_regenerated_batch_carries_push_state_and_staging(self):
        from django.utils import timezone

        from spanza_journal_watch.backend.pubmed_cache import populate_pubmed_batch_from_cache

        _, user = _make_manager()
        issue = Issue.objects.create(name="Carry Issue", body="")
        old_batch, watched = self._make_issue_batch(user, issue)

        staged_pushed = self._make_cached_article(watched, "10000001", old_batch.from_month)
        staged_unpushed = self._make_cached_article(watched, "10000002", old_batch.from_month)
        unstaged_pushed = self._make_cached_article(watched, "10000003", old_batch.from_month)

        pushed_at = timezone.now()
        PubmedBatchArticle.objects.create(
            batch=old_batch,
            article=staged_pushed,
            issue=issue,
            is_selected=True,
            planka_card_id="card-1",
            planka_card_url="https://planka.example/cards/card-1",
            planka_pushed_at=pushed_at,
        )
        PubmedBatchArticle.objects.create(batch=old_batch, article=staged_unpushed, issue=issue, is_selected=True)
        PubmedBatchArticle.objects.create(
            batch=old_batch,
            article=unstaged_pushed,
            issue=issue,
            is_selected=False,
            planka_card_id="card-3",
            planka_card_url="https://planka.example/cards/card-3",
            planka_pushed_at=pushed_at,
        )

        new_batch, _ = self._make_issue_batch(user, issue, watched=watched)
        populate_pubmed_batch_from_cache(new_batch, [watched])

        row1 = PubmedBatchArticle.objects.get(batch=new_batch, article=staged_pushed)
        assert row1.is_selected is True
        assert row1.planka_card_id == "card-1"
        assert row1.planka_pushed_at == pushed_at

        row2 = PubmedBatchArticle.objects.get(batch=new_batch, article=staged_unpushed)
        assert row2.is_selected is True
        assert row2.planka_card_id == ""

        row3 = PubmedBatchArticle.objects.get(batch=new_batch, article=unstaged_pushed)
        assert row3.is_selected is False
        assert row3.planka_card_id == "card-3"

        new_batch.refresh_from_db()
        assert new_batch.selected_count == 2

    def test_latest_batch_selection_wins_across_regenerations(self):
        from spanza_journal_watch.backend.pubmed_cache import populate_pubmed_batch_from_cache

        _, user = _make_manager()
        issue = Issue.objects.create(name="Carry Issue 2", body="")
        first_batch, watched = self._make_issue_batch(user, issue)
        article = self._make_cached_article(watched, "20000001", first_batch.from_month)

        # Staged in the first batch, then unstaged in the second.
        PubmedBatchArticle.objects.create(batch=first_batch, article=article, issue=issue, is_selected=True)
        second_batch, _ = self._make_issue_batch(user, issue, watched=watched)
        PubmedBatchArticle.objects.create(batch=second_batch, article=article, issue=issue, is_selected=False)

        third_batch, _ = self._make_issue_batch(user, issue, watched=watched)
        populate_pubmed_batch_from_cache(third_batch, [watched])

        row = PubmedBatchArticle.objects.get(batch=third_batch, article=article)
        assert row.is_selected is False


class TestArticleIntakeFilterDefaults:
    def _results(self, client, batch, **params):
        return client.get(reverse("backend:article_intake_results", kwargs={"batch_id": batch.pk}), params)

    def test_paediatric_filter_is_on_by_default(self):
        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        resp = self._results(client, batch)
        assert resp.status_code == 200
        assert resp.context["filter_paediatric_only"] is True

    def test_paediatric_filter_can_be_switched_off(self):
        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        resp = self._results(client, batch, paediatric_only="0")
        assert resp.context["filter_paediatric_only"] is False

    def test_unticking_the_box_sends_an_explicit_zero(self):
        # The hidden 0 must sit before the checkbox so the ticked value wins.
        client, user = _make_manager()
        batch, _ = _make_batch_with_watched(user)
        html = self._results(client, batch).content.decode()
        hidden = html.index('type="hidden" name="paediatric_only" value="0"')
        checkbox = html.index('name="paediatric_only" value="1" id="filter-paediatric"')
        assert hidden < checkbox


# ---------------------------------------------------------------------------
# Re-check card: where the batch's window sits relative to today
# ---------------------------------------------------------------------------


class TestIntakeRecheckState:
    @staticmethod
    def _batch(**overrides):
        from unittest.mock import Mock

        batch = Mock()
        batch.to_month = datetime.date(2026, 10, 1)
        batch.last_pubmed_fetched_at = None
        for key, value in overrides.items():
            setattr(batch, key, value)
        return batch

    @staticmethod
    def _state(batch, today):
        from django.utils import timezone

        from spanza_journal_watch.backend.views import _intake_recheck_state

        now = timezone.make_aware(datetime.datetime.combine(today, datetime.time(9, 0)))
        return _intake_recheck_state(batch, today=today, now=now)

    def test_window_end_and_settle_date_follow_to_month(self):
        state = self._state(self._batch(), datetime.date(2026, 9, 4))
        assert state["window_end"] == datetime.date(2026, 10, 31)
        assert state["settle_date"] == datetime.date(2026, 11, 14)

    def test_open_window_suggests_end_of_current_month(self):
        state = self._state(self._batch(), datetime.date(2026, 9, 4))
        assert state["phase"] == "open"
        assert state["next_check"] == datetime.date(2026, 9, 30)

    def test_open_window_final_month_suggests_window_end(self):
        state = self._state(self._batch(), datetime.date(2026, 10, 12))
        assert state["phase"] == "open"
        assert state["next_check"] == datetime.date(2026, 10, 31)

    def test_closing_phase_until_settle_date(self):
        state = self._state(self._batch(), datetime.date(2026, 11, 5))
        assert state["phase"] == "closing"
        assert state["next_check"] == datetime.date(2026, 11, 14)

    def test_settled_after_a_fortnight(self):
        state = self._state(self._batch(), datetime.date(2026, 11, 20))
        assert state["phase"] == "settled"
        assert state["next_check"] is None

    def test_never_checked_is_due(self):
        assert self._state(self._batch(), datetime.date(2026, 9, 4))["due"] is True

    def test_recent_check_is_not_due(self):
        from django.utils import timezone

        last = timezone.make_aware(datetime.datetime(2026, 9, 2, 9, 0))
        state = self._state(self._batch(last_pubmed_fetched_at=last), datetime.date(2026, 9, 4))
        assert state["due"] is False

    def test_stale_check_in_open_window_is_due(self):
        from django.utils import timezone

        last = timezone.make_aware(datetime.datetime(2026, 8, 20, 9, 0))
        state = self._state(self._batch(last_pubmed_fetched_at=last), datetime.date(2026, 9, 4))
        assert state["due"] is True

    def test_stale_check_after_settling_is_not_due(self):
        from django.utils import timezone

        last = timezone.make_aware(datetime.datetime(2026, 11, 15, 9, 0))
        state = self._state(self._batch(last_pubmed_fetched_at=last), datetime.date(2026, 12, 20))
        assert state["due"] is False
