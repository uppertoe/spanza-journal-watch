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
        self.renamed = []

    def find_user_by_email(self, email):
        return {"id": "user-1", "name": "Dr Jane Smith"}

    def update_user(self, user_id, name):
        self.renamed.append((user_id, name))
        return {"id": user_id, "name": name}

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


def test_planka_name_drops_honorific_so_initials_are_the_persons(contributor):
    client = FakePlankaClient(
        {"reviews": {"id": "m-reviews", "role": "editor"}, "instr": {"id": "m-instr", "role": "viewer"}}
    )

    _sync(client, contributor)

    assert client.renamed == [("user-1", "Jane Smith")]


def test_create_user_adds_suffix_when_username_is_taken():
    """Two addresses with the same local part must not block the second person."""
    from spanza_journal_watch.backend.planka import PlankaAPIError, PlankaClient

    client = PlankaClient.__new__(PlankaClient)
    sent = []

    def fake_request(method, path, json=None, **kwargs):
        sent.append(json["username"])
        if json["username"] in {"jo.bloggs", "jo.bloggs_1"}:
            raise PlankaAPIError("Planka API 409: Username already in use")
        return {"item": {"id": "user-2", "username": json["username"]}}

    client._request = fake_request
    user = client.create_user("jo.bloggs@health.example", "Jo Bloggs")

    assert sent == ["jo.bloggs", "jo.bloggs_1", "jo.bloggs_2"]
    assert user["username"] == "jo.bloggs_2"


def test_create_user_does_not_retry_an_email_conflict():
    from spanza_journal_watch.backend.planka import PlankaAPIError, PlankaClient

    client = PlankaClient.__new__(PlankaClient)
    calls = []

    def fake_request(method, path, json=None, **kwargs):
        calls.append(json["username"])
        raise PlankaAPIError("Planka API 409: Email already in use")

    client._request = fake_request
    with pytest.raises(PlankaAPIError, match="Email already in use"):
        client.create_user("jo.bloggs@health.example", "Jo Bloggs")
    assert calls == ["jo.bloggs"]
