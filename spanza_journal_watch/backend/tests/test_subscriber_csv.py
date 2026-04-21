"""
Integration tests: subscriber CSV upload and processing pipeline.

Covers:
  - upload_subscriber_csv: form POST with file upload creates SubscriberCSV
  - process_csv: HTMX POST confirms CSV and processes subscriber records
  - process_csv: duplicate detection (file-level and database-level)
  - process_csv: invalid / missing email rows are skipped
  - process_csv: requires HTMX header
  - process_csv: requires manage_subscriber_csv permission
"""

import io

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MANAGE_SUBSCRIBER_CSV = "backend.manage_subscriber_csv"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def _make_csv_manager():
    u = UserFactory()
    _grant(u, MANAGE_SUBSCRIBER_CSV)
    c = Client()
    c.force_login(u)
    return c, u


def _csv_file(rows, filename="subscribers.csv"):
    """Return an in-memory file-like object containing CSV rows (comma-delimited)."""
    content = "\n".join(rows).encode("utf-8")
    f = io.BytesIO(content)
    f.name = filename
    return f


def _make_subscriber_csv(rows, name="Test CSV", header=False):
    """Directly create a SubscriberCSV instance with a simple in-memory CSV."""
    from django.core.files.base import ContentFile

    content = "\n".join(rows).encode("utf-8")
    csv_obj = SubscriberCSV(name=name, header=header)
    csv_obj.file.save("test.csv", ContentFile(content), save=False)
    csv_obj.save()
    return csv_obj


# ---------------------------------------------------------------------------
# Tests: upload_subscriber_csv view
# ---------------------------------------------------------------------------


class TestUploadSubscriberCSV:
    def test_upload_creates_subscriber_csv_record(self):
        client, user = _make_csv_manager()
        # Use a comma-delimited CSV so the sniffer can detect the format
        csv_data = _csv_file(["email,name", "alice@example.com,Alice", "bob@example.com,Bob"])
        url = reverse("backend:upload_subscribers")
        resp = client.post(url, {"name": "Test list", "file": csv_data}, format="multipart")

        # Shows preview page
        assert resp.status_code == 200
        assert SubscriberCSV.objects.filter(name="Test list").exists()

    def test_upload_requires_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        csv_data = _csv_file(["alice@example.com"])
        url = reverse("backend:upload_subscribers")
        resp = c.post(url, {"name": "Unauthorized", "file": csv_data}, format="multipart")
        assert resp.status_code == 403

    def test_upload_get_shows_form(self):
        client, user = _make_csv_manager()
        url = reverse("backend:upload_subscribers")
        resp = client.get(url)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: process_csv view (HTMX endpoint)
# ---------------------------------------------------------------------------


@pytest.fixture
def _celery_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.mark.usefixtures("_celery_eager")
class TestProcessCSV:
    def test_process_csv_adds_subscribers(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(["alice@example.com", "bob@example.com"])
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        resp = client.post(url, HTTP_HX_REQUEST="true")

        assert resp.status_code == 200
        assert Subscriber.objects.filter(email="alice@example.com").exists()
        assert Subscriber.objects.filter(email="bob@example.com").exists()

    def test_process_csv_marks_csv_as_processed(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(["carol@example.com"])
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        client.post(url, HTTP_HX_REQUEST="true")

        csv_obj.refresh_from_db()
        assert csv_obj.processed is True
        assert csv_obj.confirmed is True

    def test_process_csv_skips_duplicate_in_file(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(
            [
                "dave@example.com",
                "dave@example.com",  # duplicate
                "eve@example.com",
            ]
        )
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        client.post(url, HTTP_HX_REQUEST="true")

        # dave should only appear once
        assert Subscriber.objects.filter(email="dave@example.com").count() == 1
        assert Subscriber.objects.filter(email="eve@example.com").count() == 1

    def test_process_csv_skips_already_subscribed(self):
        client, user = _make_csv_manager()
        # Pre-existing subscriber
        Subscriber.objects.create(email="frank@example.com", subscribed=True)

        csv_obj = _make_subscriber_csv(["frank@example.com", "grace@example.com"])
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        client.post(url, HTTP_HX_REQUEST="true")

        # frank should still appear only once
        assert Subscriber.objects.filter(email="frank@example.com").count() == 1
        assert Subscriber.objects.filter(email="grace@example.com").count() == 1

    def test_process_csv_skips_invalid_emails(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(
            [
                "valid@example.com",
                "not-an-email",
                "",
            ]
        )
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        client.post(url, HTTP_HX_REQUEST="true")

        assert Subscriber.objects.filter(email="valid@example.com").exists()
        assert not Subscriber.objects.filter(email="not-an-email").exists()
        assert not Subscriber.objects.filter(email="").exists()

    def test_process_csv_requires_htmx_header(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(["henry@example.com"])
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        resp = client.post(url)  # no HX-Request header

        assert resp.status_code == 400
        assert not Subscriber.objects.filter(email="henry@example.com").exists()

    def test_process_csv_requires_permission(self):
        u = UserFactory()
        c = Client()
        c.force_login(u)
        csv_obj = _make_subscriber_csv(["iris@example.com"])
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        resp = c.post(url, HTTP_HX_REQUEST="true")
        assert resp.status_code == 403

    def test_process_csv_with_header_row_detects_email_column(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(
            ["Name,Email,Phone", "Jake,jake@example.com,0400000000"],
            header=True,
        )
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        resp = client.post(url, HTTP_HX_REQUEST="true")

        assert resp.status_code == 200
        assert Subscriber.objects.filter(email="jake@example.com").exists()

    def test_process_csv_records_count_on_csv_object(self):
        client, user = _make_csv_manager()
        csv_obj = _make_subscriber_csv(
            [
                "kim@example.com",
                "lee@example.com",
                "not-valid",
            ]
        )
        url = reverse("backend:process_csv", kwargs={"save_token": csv_obj.save_token})
        client.post(url, HTTP_HX_REQUEST="true")

        csv_obj.refresh_from_db()
        assert csv_obj.row_count == 3
        assert csv_obj.email_added_count == 2

    def test_process_csv_invalid_token_returns_200(self):
        """Invalid save_token should return 200 (renders messages fragment)."""
        client, user = _make_csv_manager()
        url = reverse("backend:process_csv", kwargs={"save_token": "bad-token-xyz"})
        resp = client.post(url, HTTP_HX_REQUEST="true")
        assert resp.status_code == 200
