"""Dashboard workflow steps: which parts of an issue count as done."""

import pytest
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import IssueContributor, PlankaIssueBinding
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _steps(client):
    response = client.get(reverse("backend:dashboard"))
    assert response.status_code == 200
    return dict(response.context["workflow_steps"])


@pytest.fixture
def chief_client():
    user = UserFactory(is_superuser=True, is_staff=True)
    client = Client()
    client.force_login(user)
    return client


def test_fresh_issue_has_no_steps_done(chief_client):
    Issue.objects.create(name="December 2026", active=False)
    steps = _steps(chief_client)
    assert list(steps) == ["Setup", "Articles", "Reviewers", "Reviews", "Published", "Newsletter"]
    assert not any(steps.values())


def test_coordinator_alone_does_not_tick_reviewers(chief_client):
    issue = Issue.objects.create(name="December 2026", active=False)
    IssueContributor.objects.create(issue=issue, email="coord@example.com", role=IssueContributor.Role.COORDINATOR)
    assert _steps(chief_client)["Reviewers"] is False


def test_reviewer_ticks_reviewers_unless_revoked(chief_client):
    issue = Issue.objects.create(name="December 2026", active=False)
    reviewer = IssueContributor.objects.create(
        issue=issue, email="rev@example.com", role=IssueContributor.Role.REVIEWER
    )
    assert _steps(chief_client)["Reviewers"] is True
    reviewer.status = IssueContributor.Status.REVOKED
    reviewer.save()
    assert _steps(chief_client)["Reviewers"] is False


def test_planka_board_ticks_setup(chief_client):
    issue = Issue.objects.create(name="December 2026", active=False)
    PlankaIssueBinding.objects.create(issue=issue, project_id="p1", project_name="December 2026", board_id="b1")
    assert _steps(chief_client)["Setup"] is True
