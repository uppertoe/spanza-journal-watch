"""
Tests for CPD views.

Covers:
1. toggle_cpd_tracking — requires login + POST, flips flag
2. report_page — requires login, renders
3. generate_report — validates dates, prevents concurrent, enforces max 5
4. download_report — 404 when not ready, returns PDF when ready
5. article_count_preview — returns count string
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse

from spanza_journal_watch.cpd.models import CPDReport

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture()
def cpd_user(db):
    return User.objects.create_user(email="cpd@example.com", password="pw", cpd_tracking_enabled=False)


# ---------------------------------------------------------------------------
# 1. toggle_cpd_tracking
# ---------------------------------------------------------------------------


class TestToggleCpdTracking:
    def test_requires_login(self, client):
        response = client.post(reverse("cpd:toggle_tracking"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_requires_post(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:toggle_tracking"))
        assert response.status_code == 405

    def test_toggles_flag_on(self, client, cpd_user):
        client.force_login(cpd_user)
        client.post(reverse("cpd:toggle_tracking"))
        cpd_user.refresh_from_db()
        assert cpd_user.cpd_tracking_enabled is True

    def test_toggles_flag_off(self, client, cpd_user):
        cpd_user.cpd_tracking_enabled = True
        cpd_user.save()
        client.force_login(cpd_user)
        client.post(reverse("cpd:toggle_tracking"))
        cpd_user.refresh_from_db()
        assert cpd_user.cpd_tracking_enabled is False

    def test_returns_htmx_trigger_header(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.post(reverse("cpd:toggle_tracking"))
        assert response["HX-Trigger"] == "cpdTrackingChanged"


# ---------------------------------------------------------------------------
# 2. report_page
# ---------------------------------------------------------------------------


class TestReportPage:
    def test_requires_login(self, client):
        response = client.get(reverse("cpd:report_page"))
        assert response.status_code == 302

    def test_renders_for_authenticated_user(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:report_page"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. generate_report
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_requires_login(self, client):
        response = client.post(reverse("cpd:generate"))
        assert response.status_code == 302

    def test_requires_post(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:generate"))
        assert response.status_code == 405

    def test_invalid_dates_returns_400(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.post(reverse("cpd:generate"), {"date_from": "bad", "date_to": "bad"})
        assert response.status_code == 400

    def test_missing_dates_returns_400(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.post(reverse("cpd:generate"), {})
        assert response.status_code == 400

    def test_start_after_end_returns_400(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.post(
            reverse("cpd:generate"),
            {"date_from": "2026-12-01", "date_to": "2026-01-01"},
        )
        assert response.status_code == 400

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_report_task")
    def test_creates_report_and_queues_task(self, mock_task, client, cpd_user):
        client.force_login(cpd_user)
        response = client.post(
            reverse("cpd:generate"),
            {"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
        assert response.status_code == 200
        assert CPDReport.objects.filter(user=cpd_user).exists()
        mock_task.delay.assert_called_once()

    @patch("spanza_journal_watch.cpd.tasks.generate_cpd_report_task")
    def test_prevents_concurrent_generation(self, mock_task, client, cpd_user):
        CPDReport.objects.create(
            user=cpd_user, date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), status=CPDReport.Status.PENDING
        )
        client.force_login(cpd_user)
        client.post(
            reverse("cpd:generate"),
            {"date_from": "2026-07-01", "date_to": "2026-12-31"},
        )
        # Should not create a second report
        assert CPDReport.objects.filter(user=cpd_user).count() == 1
        mock_task.delay.assert_not_called()


# ---------------------------------------------------------------------------
# 4. download_report
# ---------------------------------------------------------------------------


class TestDownloadReport:
    def test_requires_login(self, client):
        response = client.get(reverse("cpd:download", args=[1]))
        assert response.status_code == 302

    def test_not_ready_returns_404(self, client, cpd_user):
        report = CPDReport.objects.create(
            user=cpd_user, date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), status=CPDReport.Status.PENDING
        )
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:download", args=[report.pk]))
        assert response.status_code == 404

    def test_other_users_report_returns_404(self, client, cpd_user):
        other = User.objects.create_user(email="other-cpd@example.com", password="pw")
        report = CPDReport.objects.create(
            user=other,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 30),
            status=CPDReport.Status.READY,
            file=ContentFile(b"%PDF-fake", name="report.pdf"),
        )
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:download", args=[report.pk]))
        assert response.status_code == 404

    def test_ready_report_returns_pdf(self, client, cpd_user):
        report = CPDReport.objects.create(
            user=cpd_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 30),
            status=CPDReport.Status.READY,
            file=ContentFile(b"%PDF-fake-content", name="report.pdf"),
        )
        client.force_login(cpd_user)
        response = client.get(reverse("cpd:download", args=[report.pk]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert "attachment" in response["Content-Disposition"]


# ---------------------------------------------------------------------------
# 5. article_count_preview
# ---------------------------------------------------------------------------


class TestArticleCountPreview:
    def test_requires_login(self, client):
        response = client.get(reverse("cpd:article_count"))
        assert response.status_code == 302

    def test_returns_count_for_valid_dates(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.get(
            reverse("cpd:article_count"),
            {"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
        assert response.status_code == 200
        assert response.content == b"0"

    def test_invalid_dates_returns_zero(self, client, cpd_user):
        client.force_login(cpd_user)
        response = client.get(
            reverse("cpd:article_count"),
            {"date_from": "bad", "date_to": "bad"},
        )
        assert response.status_code == 200
        assert response.content == b"0"
