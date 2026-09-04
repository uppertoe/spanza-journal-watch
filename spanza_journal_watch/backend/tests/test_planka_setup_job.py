"""Planka project setup runs in Celery and the setup card polls for the result."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from spanza_journal_watch.backend.models import PlankaIntegrationCredential, PlankaIssueBinding, PlankaProjectSetupJob
from spanza_journal_watch.backend.planka import PlankaAPIError
from spanza_journal_watch.backend.tasks import provision_planka_project_task
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class FakePlankaClient:
    configured = True

    def __init__(self, fail=False):
        self.fail = fail

    def create_project(self, name):
        if self.fail:
            raise PlankaAPIError("Planka said no")
        return {"id": "project-1", "name": name}

    def create_board(self, project_id, name, position=None):
        return {"id": "board-1" if name == "Reviews" else "board-2", "name": name}

    def create_list(self, board_id, name, position, list_type="active"):
        return {"id": f"list-{name.lower().replace(' ', '-')}", "name": name}

    def update_list(self, list_id, **kwargs):
        return {}

    def create_card(self, **kwargs):
        return {"id": "card"}

    def list_webhooks(self):
        return []

    def create_webhook(self, *args, **kwargs):
        return {"id": "wh-1"}


def _chief_editor_client(client):
    user = UserFactory()
    user.user_permissions.add(
        Permission.objects.get(codename="chief_editor"),
        Permission.objects.get(codename="manage_issue_builder"),
    )
    client.force_login(user)
    return user


def _setup_url(issue):
    return reverse("backend:planka_setup_issue_project", kwargs={"issue_id": issue.pk})


def _status_url(issue, variant="setup"):
    return reverse("backend:planka_setup_issue_project_status", kwargs={"issue_id": issue.pk}) + f"?variant={variant}"


class TestSetupRequest:
    def test_post_queues_job_and_returns_polling_card(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Setup Issue", body="body")

        with patch("spanza_journal_watch.backend.views.provision_planka_project_task.delay") as delay:
            response = client.post(
                _setup_url(issue), data={"project_name": "Setup Issue", "from_setup_page": "1"}, HTTP_HX_REQUEST="true"
            )

        assert response.status_code == 200
        job = PlankaProjectSetupJob.objects.get(issue=issue)
        assert job.state == PlankaProjectSetupJob.STATE_PENDING
        assert job.project_name == "Setup Issue"
        delay.assert_called_once_with(job.pk)
        body = response.content.decode()
        assert 'id="planka-sync-panel"' in body
        assert _status_url(issue) in body
        assert 'hx-trigger="every 1s"' in body
        assert not PlankaIssueBinding.objects.filter(issue=issue).exists()

    def test_post_without_name_uses_issue_name(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="October 2026", body="body")

        with patch("spanza_journal_watch.backend.views.provision_planka_project_task.delay"):
            response = client.post(_setup_url(issue), data={"from_setup_page": "1"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert PlankaProjectSetupJob.objects.get(issue=issue).project_name == "October 2026"

    def test_second_post_while_running_does_not_requeue(self, client):
        _chief_editor_client(client)
        issue = Issue.objects.create(name="Busy Issue", body="body")
        PlankaProjectSetupJob.objects.create(issue=issue, project_name="x", state=PlankaProjectSetupJob.STATE_RUNNING)

        with patch("spanza_journal_watch.backend.views.provision_planka_project_task.delay") as delay:
            response = client.post(
                _setup_url(issue), data={"project_name": "again", "from_setup_page": "1"}, HTTP_HX_REQUEST="true"
            )

        assert response.status_code == 200
        delay.assert_not_called()
        assert 'hx-trigger="every 1s"' in response.content.decode()

    def test_setup_card_shows_progress_on_full_page_load(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Reloaded Issue", body="body")
        PlankaProjectSetupJob.objects.create(issue=issue, project_name="x", state=PlankaProjectSetupJob.STATE_PENDING)

        response = client.get(reverse("backend:issue_builder") + f"?issue={issue.pk}")

        body = response.content.decode()
        assert 'hx-trigger="every 1s"' in body
        assert "Create Planka board" not in body

    def test_form_carries_timeout_and_indicator(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Fresh Issue", body="body")

        body = client.get(reverse("backend:issue_builder") + f"?issue={issue.pk}").content.decode()

        assert "hx-request='{\"timeout\": 120000}'" in body
        assert 'hx-disabled-elt="find button[type=submit]"' in body
        assert 'hx-indicator="#planka-setup-progress"' in body


class TestStatusPolling:
    def test_status_returns_connected_card_once_bound(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Bound Issue", body="body")
        PlankaProjectSetupJob.objects.create(issue=issue, project_name="x", state=PlankaProjectSetupJob.STATE_SUCCESS)
        PlankaIssueBinding.objects.create(issue=issue, project_id="p1", project_name="Bound Issue", board_id="b1")

        body = client.get(_status_url(issue)).content.decode()

        assert "Connected" in body
        assert "every 1s" not in body

    def test_status_keeps_polling_while_running(self, client):
        _chief_editor_client(client)
        issue = Issue.objects.create(name="Running Issue", body="body")
        PlankaProjectSetupJob.objects.create(issue=issue, project_name="x", state=PlankaProjectSetupJob.STATE_RUNNING)

        body = client.get(_status_url(issue)).content.decode()

        assert 'hx-trigger="every 1s"' in body

    def test_status_shows_error_and_form_after_failure(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Failed Issue", body="body")
        PlankaProjectSetupJob.objects.create(
            issue=issue,
            project_name="x",
            state=PlankaProjectSetupJob.STATE_ERROR,
            note="Unable to set up Planka project: Planka said no",
        )

        body = client.get(_status_url(issue)).content.decode()

        assert "Planka said no" in body
        assert "Create Planka board" in body
        assert "every 1s" not in body

    def test_stale_running_job_is_reported_not_polled_forever(self, client):
        _chief_editor_client(client)
        PlankaIntegrationCredential.objects.create(api_key="test-api-key")
        issue = Issue.objects.create(name="Stale Issue", body="body")
        job = PlankaProjectSetupJob.objects.create(
            issue=issue, project_name="x", state=PlankaProjectSetupJob.STATE_RUNNING
        )
        PlankaProjectSetupJob.objects.filter(pk=job.pk).update(
            modified=job.modified - PlankaProjectSetupJob.STALE_AFTER * 2
        )

        body = client.get(_status_url(issue)).content.decode()

        assert "did not finish" in body
        assert "every 1s" not in body


class TestProvisionTask:
    def test_task_creates_binding_and_marks_success(self, monkeypatch):
        issue = Issue.objects.create(name="Task Issue", body="body")
        job = PlankaProjectSetupJob.objects.create(issue=issue, project_name="Task Issue")
        monkeypatch.setattr("spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaClient())

        provision_planka_project_task.apply(args=(job.pk,))

        job.refresh_from_db()
        assert job.state == PlankaProjectSetupJob.STATE_SUCCESS
        binding = PlankaIssueBinding.objects.get(issue=issue)
        assert binding.project_id == "project-1"
        assert binding.board_id == "board-1"
        assert binding.instructions_board_id == "board-2"
        assert binding.get_list_id("publish_ready") == "list-publish-ready"

    def test_task_records_planka_error(self, monkeypatch):
        issue = Issue.objects.create(name="Task Error Issue", body="body")
        job = PlankaProjectSetupJob.objects.create(issue=issue, project_name="Task Error Issue")
        monkeypatch.setattr(
            "spanza_journal_watch.backend.views._build_planka_client", lambda: FakePlankaClient(fail=True)
        )

        provision_planka_project_task.apply(args=(job.pk,))

        job.refresh_from_db()
        assert job.state == PlankaProjectSetupJob.STATE_ERROR
        assert "Planka said no" in job.note
        assert not PlankaIssueBinding.objects.filter(issue=issue).exists()

    def test_task_never_leaves_job_running_on_unexpected_error(self, monkeypatch):
        issue = Issue.objects.create(name="Task Crash Issue", body="body")
        job = PlankaProjectSetupJob.objects.create(issue=issue, project_name="Task Crash Issue")

        def boom():
            raise RuntimeError("worker exploded")

        monkeypatch.setattr("spanza_journal_watch.backend.views._build_planka_client", boom)

        provision_planka_project_task.apply(args=(job.pk,))

        job.refresh_from_db()
        assert job.state == PlankaProjectSetupJob.STATE_ERROR
        assert "unexpected error" in job.note
