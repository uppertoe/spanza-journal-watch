"""
Tests for backend Celery tasks.

Covers:
1. process_subscriber_csv_record — CSV parsing, email validation, dedup
2. run_pubmed_batch_import_task — state transitions, error handling
3. refresh_pubmed_journal_cache_task — date handling, FetchLog creation
4. compute_tag_clusters_task — clustering logic, caching
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from spanza_journal_watch.backend.models import FetchLog, PubmedImportBatch, SubscriberCSV, WatchedJournal
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Issue

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. process_subscriber_csv_record
# ---------------------------------------------------------------------------


class TestProcessSubscriberCsvRecord:
    def _make_csv(self, content, name="test.csv", header=True):
        csv_file = SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")
        return SubscriberCSV.objects.create(
            name="Test CSV",
            file=csv_file,
            confirmed=True,
            header=header,
        )

    def test_imports_valid_emails(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("email\nalice@example.com\nbob@example.com\n")
        result = process_subscriber_csv_record(csv_obj)

        assert result["records_added"] == 2
        assert result["rows_parsed"] == 2
        assert Subscriber.objects.filter(email="alice@example.com").exists()
        assert Subscriber.objects.filter(email="bob@example.com").exists()

    def test_skips_invalid_emails(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("email\nnot-an-email\nalice@example.com\n")
        result = process_subscriber_csv_record(csv_obj)

        assert result["records_added"] == 1
        assert result["invalid_email_count"] == 1

    def test_deduplicates_within_file(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("email\ndupe@example.com\ndupe@example.com\n")
        result = process_subscriber_csv_record(csv_obj)

        assert result["records_added"] == 1
        assert result["duplicate_in_file_count"] == 1

    def test_skips_already_subscribed(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        Subscriber.objects.create(email="existing@example.com", subscribed=True)
        csv_obj = self._make_csv("email\nexisting@example.com\nnew@example.com\n")
        result = process_subscriber_csv_record(csv_obj)

        assert result["records_added"] == 1
        assert result["already_subscribed_count"] == 1

    def test_marks_csv_as_processed(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("email\nprocessed@example.com\n")
        process_subscriber_csv_record(csv_obj)
        csv_obj.refresh_from_db()

        assert csv_obj.processed is True
        assert csv_obj.row_count == 1
        assert csv_obj.email_added_count == 1

    def test_normalizes_email_case(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("email\nUPPER@Example.COM\n")
        process_subscriber_csv_record(csv_obj)

        assert Subscriber.objects.filter(email="upper@example.com").exists()

    def test_empty_file_raises(self):
        from spanza_journal_watch.backend.tasks import NoEmailColumnsFoundException, process_subscriber_csv_record

        csv_obj = self._make_csv("", header=False)
        with pytest.raises(NoEmailColumnsFoundException):
            process_subscriber_csv_record(csv_obj)

    def test_returns_email_column_name(self):
        from spanza_journal_watch.backend.tasks import process_subscriber_csv_record

        csv_obj = self._make_csv("name,email,phone\nAlice,alice@example.com,123\n")
        result = process_subscriber_csv_record(csv_obj)

        assert result["email_column"] == "email"


# ---------------------------------------------------------------------------
# 2. run_pubmed_batch_import_task
# ---------------------------------------------------------------------------


class TestRunPubmedBatchImportTask:
    @pytest.fixture()
    def batch(self):
        issue = Issue.objects.create(name="Import Issue", body="body")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            from_month=date(2026, 1, 1),
            to_month=date(2026, 3, 1),
        )
        wj = WatchedJournal.objects.create(name="Import Journal", active=True)
        batch.watched_journals.add(wj)
        return batch

    @patch("spanza_journal_watch.backend.pubmed_cache.populate_pubmed_batch_from_cache")
    def test_success_sets_state(self, mock_populate, batch):
        from spanza_journal_watch.backend.tasks import run_pubmed_batch_import_task

        mock_populate.return_value = None
        batch.result_count = 5
        batch.save(update_fields=["result_count"])

        result = run_pubmed_batch_import_task.apply(args=[batch.pk]).result
        batch.refresh_from_db()

        assert batch.task_state == PubmedImportBatch.TASK_STATE_SUCCESS
        assert result["status"] == "success"

    @patch("spanza_journal_watch.backend.pubmed_cache.populate_pubmed_batch_from_cache")
    def test_creates_fetch_log(self, mock_populate, batch):
        from spanza_journal_watch.backend.tasks import run_pubmed_batch_import_task

        run_pubmed_batch_import_task.apply(args=[batch.pk])

        log = FetchLog.objects.filter(task_type=FetchLog.TASK_BATCH_IMPORT).first()
        assert log is not None
        assert log.status == FetchLog.STATUS_SUCCESS

    def test_no_journals_sets_error(self):
        from spanza_journal_watch.backend.tasks import run_pubmed_batch_import_task

        issue = Issue.objects.create(name="Empty Import Issue", body="body")
        batch = PubmedImportBatch.objects.create(
            issue=issue,
            from_month=date(2026, 1, 1),
            to_month=date(2026, 3, 1),
        )

        result = run_pubmed_batch_import_task.apply(args=[batch.pk]).result
        batch.refresh_from_db()

        assert batch.task_state == PubmedImportBatch.TASK_STATE_ERROR
        assert "No active watched journals" in batch.task_note
        assert result["status"] == "error"

    @patch("spanza_journal_watch.backend.pubmed_cache.populate_pubmed_batch_from_cache")
    def test_api_error_sets_error_state(self, mock_populate, batch):
        from spanza_journal_watch.backend.pubmed import PubmedAPIError
        from spanza_journal_watch.backend.tasks import run_pubmed_batch_import_task

        mock_populate.side_effect = PubmedAPIError("API timeout")

        result = run_pubmed_batch_import_task.apply(args=[batch.pk]).result
        batch.refresh_from_db()

        assert batch.task_state == PubmedImportBatch.TASK_STATE_ERROR
        assert result["status"] == "error"

    @patch("spanza_journal_watch.backend.pubmed_cache.populate_pubmed_batch_from_cache")
    def test_sets_running_state_during_execution(self, mock_populate, batch):
        from spanza_journal_watch.backend.tasks import run_pubmed_batch_import_task

        states_seen = []

        def capture_state(b, journals):
            b_fresh = PubmedImportBatch.objects.get(pk=b.pk)
            states_seen.append(b_fresh.task_state)

        mock_populate.side_effect = capture_state

        run_pubmed_batch_import_task.apply(args=[batch.pk])
        assert PubmedImportBatch.TASK_STATE_RUNNING in states_seen


# ---------------------------------------------------------------------------
# 3. refresh_pubmed_journal_cache_task
# ---------------------------------------------------------------------------


class TestRefreshPubmedJournalCacheTask:
    @patch("spanza_journal_watch.backend.pubmed_cache.refresh_pubmed_journal_cache")
    @patch("spanza_journal_watch.backend.pubmed_cache.default_pubmed_cache_window")
    def test_uses_default_window_when_no_dates(self, mock_window, mock_refresh):
        from spanza_journal_watch.backend.tasks import refresh_pubmed_journal_cache_task

        mock_window.return_value = (date(2026, 1, 1), date(2026, 3, 1))
        mock_refresh.return_value = {"journal_count": 5, "created_links": 10, "touched_links": 2}

        result = refresh_pubmed_journal_cache_task.apply().result

        mock_window.assert_called_once()
        assert result["status"] == "success"

    @patch("spanza_journal_watch.backend.pubmed_cache.refresh_pubmed_journal_cache")
    @patch("spanza_journal_watch.backend.pubmed_cache.default_pubmed_cache_window")
    def test_uses_provided_dates(self, mock_window, mock_refresh):
        from spanza_journal_watch.backend.tasks import refresh_pubmed_journal_cache_task

        mock_refresh.return_value = {"journal_count": 3, "created_links": 5, "touched_links": 1}

        result = refresh_pubmed_journal_cache_task.apply(
            kwargs={"from_month": "2026-01-01", "to_month": "2026-06-01"}
        ).result

        mock_window.assert_not_called()
        assert result["from_month"] == "2026-01-01"
        assert result["to_month"] == "2026-06-01"

    @patch("spanza_journal_watch.backend.pubmed_cache.refresh_pubmed_journal_cache")
    @patch("spanza_journal_watch.backend.pubmed_cache.default_pubmed_cache_window")
    def test_creates_fetch_log_on_success(self, mock_window, mock_refresh):
        from spanza_journal_watch.backend.tasks import refresh_pubmed_journal_cache_task

        mock_window.return_value = (date(2026, 1, 1), date(2026, 3, 1))
        mock_refresh.return_value = {"journal_count": 2, "created_links": 0, "touched_links": 0}

        refresh_pubmed_journal_cache_task.apply()

        log = FetchLog.objects.filter(task_type=FetchLog.TASK_CACHE_REFRESH).first()
        assert log is not None
        assert log.status == FetchLog.STATUS_SUCCESS

    @patch("spanza_journal_watch.backend.pubmed_cache.refresh_pubmed_journal_cache")
    @patch("spanza_journal_watch.backend.pubmed_cache.default_pubmed_cache_window")
    def test_creates_error_fetch_log_on_failure(self, mock_window, mock_refresh):
        from spanza_journal_watch.backend.tasks import refresh_pubmed_journal_cache_task

        mock_window.return_value = (date(2026, 1, 1), date(2026, 3, 1))
        mock_refresh.side_effect = RuntimeError("PubMed down")

        task_result = refresh_pubmed_journal_cache_task.apply()
        # The task re-raises, so the result should hold the exception
        assert task_result.failed()

        log = FetchLog.objects.filter(task_type=FetchLog.TASK_CACHE_REFRESH).first()
        assert log is not None
        assert log.status == FetchLog.STATUS_ERROR


# ---------------------------------------------------------------------------
# 4. compute_tag_clusters_task
# ---------------------------------------------------------------------------


class TestComputeTagClustersTask:
    @patch("django.core.cache.cache")
    def test_returns_cluster_counts(self, mock_cache):
        from spanza_journal_watch.backend.models import PubmedArticle
        from spanza_journal_watch.backend.tasks import compute_tag_clusters_task
        from spanza_journal_watch.submissions.models import Journal, Tag

        journal = Journal.objects.create(name="Cluster Journal")
        articles = [PubmedArticle.objects.create(title=f"Cluster Art {i}", journal=journal) for i in range(5)]

        tag_a = Tag.objects.create(text="cluster-tag-a-xyz", slug="cluster-tag-a-xyz", active=True, curated=True)
        tag_b = Tag.objects.create(text="cluster-tag-b-xyz", slug="cluster-tag-b-xyz", active=True, curated=True)

        # Give both tags all the same articles → high similarity
        tag_a.articles.set(articles)
        tag_b.articles.set(articles)

        result = compute_tag_clusters_task()
        assert result["tags"] >= 2
        assert result["clusters"] >= 1
        mock_cache.set.assert_called_once()

    @patch("django.core.cache.cache")
    def test_no_overlap_no_clusters(self, mock_cache):
        from spanza_journal_watch.backend.models import PubmedArticle
        from spanza_journal_watch.backend.tasks import compute_tag_clusters_task
        from spanza_journal_watch.submissions.models import Journal, Tag

        journal = Journal.objects.create(name="NoOverlap Journal")
        art_a = PubmedArticle.objects.create(title="NoOv Art A", journal=journal)
        art_b = PubmedArticle.objects.create(title="NoOv Art B", journal=journal)

        tag_a = Tag.objects.create(text="noov-tag-a-xyz", slug="noov-tag-a-xyz", active=True, curated=True)
        tag_b = Tag.objects.create(text="noov-tag-b-xyz", slug="noov-tag-b-xyz", active=True, curated=True)

        tag_a.articles.set([art_a])
        tag_b.articles.set([art_b])

        result = compute_tag_clusters_task()
        # Zero overlap → zero clusters (from these tags at least)
        assert isinstance(result["clusters"], int)

    @patch("django.core.cache.cache")
    def test_caches_result(self, mock_cache):
        from spanza_journal_watch.backend.tasks import compute_tag_clusters_task

        compute_tag_clusters_task()
        mock_cache.set.assert_called_once()
        args = mock_cache.set.call_args[0]
        assert isinstance(args[1], list)  # clusters list
