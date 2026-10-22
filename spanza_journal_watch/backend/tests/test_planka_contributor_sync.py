"""Syncing a contributor to Planka keeps their board memberships, and with them their cards."""

from unittest.mock import patch

import pytest

from spanza_journal_watch.backend.models import IssueContributor, PlankaIssueBinding
from spanza_journal_watch.backend.views.planka_boards import _sync_contributor_to_planka
from spanza_journal_watch.submissions.models import Issue

pytestmark = pytest.mark.django_db


class FakePlankaClient:
    def __init__(self, memberships=None):
        # {board_id: {"id": ..., "role": ...}}
        self.memberships = dict(memberships or {})
        self.removed = []
        self.added = []
        self.updated = []

    def find_user_by_email(self, email):
        return {"id": "user-1", "name": "Dr Jane Smith"}

    def find_board_membership(self, board_id, user_id):
        return self.memberships.get(board_id)

    def add_board_member(self, board_id, user_id, role="editor"):
        membership = {"id": f"new-{board_id}", "role": role}
        self.memberships[board_id] = membership
        self.added.append((board_id, role))
        return membership

    def update_board_member(self, membership_id, role):
        self.updated.append((membership_id, role))
        return {"id": membership_id, "role": role}

    def remove_board_member(self, membership_id):
        self.removed.append(membership_id)
        return True


@pytest.fixture
def contributor():
    issue = Issue.objects.create(name="October 2026", active=False)
    PlankaIssueBinding.objects.create(
        issue=issue, project_id="p1", project_name="October 2026", board_id="reviews", instructions_board_id="instr"
    )
    return IssueContributor.objects.create(
        issue=issue,
        email="jane@example.com",
        name="Dr Jane Smith",
        role=IssueContributor.Role.REVIEWER,
        planka_membership_id="m-reviews",
        planka_instructions_membership_id="m-instr",
    )


def _sync(client, contributor):
    with patch("spanza_journal_watch.backend.views.shared._build_planka_client", return_value=client):
        return _sync_contributor_to_planka(contributor)


def test_resync_keeps_existing_memberships(contributor):
    client = FakePlankaClient(
        {"reviews": {"id": "m-reviews", "role": "editor"}, "instr": {"id": "m-instr", "role": "viewer"}}
    )

    assert _sync(client, contributor) == (True, "")

    assert client.removed == []
    assert client.added == []
    assert client.updated == []
    contributor.refresh_from_db()
    assert contributor.planka_membership_id == "m-reviews"
    assert contributor.planka_instructions_membership_id == "m-instr"


def test_sync_adds_missing_memberships(contributor):
    client = FakePlankaClient()

    _sync(client, contributor)

    assert client.removed == []
    assert client.added == [("reviews", "editor"), ("instr", "viewer")]
    contributor.refresh_from_db()
    assert contributor.planka_membership_id == "new-reviews"
    assert contributor.planka_instructions_membership_id == "new-instr"


def test_sync_corrects_role_in_place(contributor):
    contributor.role = IssueContributor.Role.COORDINATOR
    contributor.save()
    client = FakePlankaClient(
        {"reviews": {"id": "m-reviews", "role": "editor"}, "instr": {"id": "m-instr", "role": "viewer"}}
    )

    _sync(client, contributor)

    assert client.removed == []
    assert client.updated == [("m-instr", "editor")]
