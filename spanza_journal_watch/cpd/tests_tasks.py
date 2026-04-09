"""
Tests for CPD Celery tasks.

Covers:
1. generate_cpd_report_task — status transitions, PDF generation, error handling
2. cleanup_expired_cpd_reports — deletes old reports and files
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone

from spanza_journal_watch.cpd.models import CPDReport

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture()
def cpd_user():
    return User.objects.create_user(email="cpd-task@example.com", password="pw", name="Dr Task")


@pytest.fixture()
def pending_report(cpd_user):
    return CPDReport.objects.create(
        user=cpd_user,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 6, 30),
        status=CPDReport.Status.PENDING,
    )


# ---------------------------------------------------------------------------
# 1. generate_cpd_report_task
# ---------------------------------------------------------------------------


class TestGenerateCpdReportTask:
    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_success_sets_status_ready(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        generate_cpd_report_task(pending_report.pk)
        pending_report.refresh_from_db()
        assert pending_report.status == CPDReport.Status.READY

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_success_saves_pdf_file(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        generate_cpd_report_task(pending_report.pk)
        pending_report.refresh_from_db()
        assert pending_report.file
        assert "cpd_report_" in pending_report.file.name

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_success_sets_article_count(self, mock_pdf, pending_report, cpd_user):
        from spanza_journal_watch.backend.models import PubmedArticle, PubmedArticleUserState
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task
        from spanza_journal_watch.submissions.models import Journal

        journal = Journal.objects.create(name="CPD Journal")
        article = PubmedArticle.objects.create(title="CPD Article", journal=journal)
        PubmedArticleUserState.objects.create(
            user=cpd_user,
            article=article,
            full_text_clicked_at=timezone.make_aware(timezone.datetime(2026, 3, 15, 12, 0, 0)),
        )

        generate_cpd_report_task(pending_report.pk)
        pending_report.refresh_from_db()
        assert pending_report.article_count == 1

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_transitions_through_generating_status(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        statuses_seen = []

        original_save = CPDReport.save

        def tracking_save(self, *args, **kwargs):
            statuses_seen.append(self.status)
            original_save(self, *args, **kwargs)

        with patch.object(CPDReport, "save", tracking_save):
            generate_cpd_report_task(pending_report.pk)

        assert CPDReport.Status.GENERATING in statuses_seen

    def test_nonexistent_report_returns_silently(self):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        # Should not raise
        generate_cpd_report_task(999999)

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", side_effect=RuntimeError("PDF boom"))
    def test_error_sets_status_error(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        with pytest.raises(RuntimeError):
            generate_cpd_report_task(pending_report.pk)

        pending_report.refresh_from_db()
        assert pending_report.status == CPDReport.Status.ERROR
        assert pending_report.error_message != ""

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_zero_articles_still_generates(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        generate_cpd_report_task(pending_report.pk)
        pending_report.refresh_from_db()
        assert pending_report.status == CPDReport.Status.READY
        assert pending_report.article_count == 0
        mock_pdf.assert_called_once()

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_uses_user_name_for_pdf(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        generate_cpd_report_task(pending_report.pk)
        call_kwargs = mock_pdf.call_args[1]
        assert call_kwargs["user_name"] == "Dr Task"

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_pdf", return_value=b"%PDF-fake")
    def test_falls_back_to_email_when_no_name(self, mock_pdf, pending_report):
        from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

        pending_report.user.name = ""
        pending_report.user.save(update_fields=["name"])

        generate_cpd_report_task(pending_report.pk)
        call_kwargs = mock_pdf.call_args[1]
        assert call_kwargs["user_name"] == "cpd-task@example.com"


# ---------------------------------------------------------------------------
# 2. cleanup_expired_cpd_reports
# ---------------------------------------------------------------------------


class TestCleanupExpiredCpdReports:
    def test_deletes_old_reports(self, cpd_user):
        from spanza_journal_watch.cpd.tasks import cleanup_expired_cpd_reports

        old = CPDReport.objects.create(
            user=cpd_user,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 6, 30),
            status=CPDReport.Status.READY,
        )
        # Backdate the created timestamp
        CPDReport.objects.filter(pk=old.pk).update(created=timezone.now() - timedelta(days=31))

        cleanup_expired_cpd_reports()
        assert not CPDReport.objects.filter(pk=old.pk).exists()

    def test_preserves_recent_reports(self, cpd_user):
        from spanza_journal_watch.cpd.tasks import cleanup_expired_cpd_reports

        recent = CPDReport.objects.create(
            user=cpd_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 30),
            status=CPDReport.Status.READY,
        )

        cleanup_expired_cpd_reports()
        assert CPDReport.objects.filter(pk=recent.pk).exists()

    def test_deletes_files_from_storage(self, cpd_user):
        from spanza_journal_watch.cpd.tasks import cleanup_expired_cpd_reports

        report = CPDReport.objects.create(
            user=cpd_user,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 6, 30),
            status=CPDReport.Status.READY,
            file=ContentFile(b"%PDF-fake", name="old_report.pdf"),
        )
        CPDReport.objects.filter(pk=report.pk).update(created=timezone.now() - timedelta(days=31))

        cleanup_expired_cpd_reports()
        assert not CPDReport.objects.filter(pk=report.pk).exists()
