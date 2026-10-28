"""A deleted account loses its Planka board places and sign-in; its cards and comments stay."""

from unittest.mock import MagicMock, patch

import pytest

from spanza_journal_watch.backend.models import PlankaIssueBinding
from spanza_journal_watch.backend.planka import PlankaAPIError, PlankaClient
from spanza_journal_watch.backend.tasks import withdraw_planka_access_for_deleted_account
from spanza_journal_watch.submissions.models import Issue

pytestmark = pytest.mark.django_db

BUILD_CLIENT = "spanza_journal_watch.backend.views.shared._build_planka_client"


@pytest.fixture
def issue():
    issue = Issue.objects.create(name="October 2026", active=False)
    PlankaIssueBinding.objects.create(
        issue=issue, project_id="p1", project_name="October 2026", board_id="reviews", instructions_board_id="instr"
    )
    return issue


def _membership(issue, **overrides):
    entry = {"issue_id": issue.pk, "planka_user_id": "u-1", "membership_id": "", "instructions_membership_id": ""}
    entry.update(overrides)
    return entry


def test_stored_memberships_are_removed_and_the_user_deactivated(issue):
    client = MagicMock()
    with patch(BUILD_CLIENT, return_value=client):
        withdraw_planka_access_for_deleted_account(
            7, [_membership(issue, membership_id="m-1", instructions_membership_id="m-2")]
        )

    assert [call.args[0] for call in client.remove_board_member.call_args_list] == ["m-1", "m-2"]
    client.find_board_membership.assert_not_called()
    client.deactivate_user.assert_called_once_with("u-1")
    client.delete_card.assert_not_called()


def test_missing_membership_ids_are_looked_up_on_both_boards(issue):
    client = MagicMock()
    client.find_board_membership.side_effect = lambda board_id, user_id: {"id": f"found-{board_id}"}
    with patch(BUILD_CLIENT, return_value=client):
        withdraw_planka_access_for_deleted_account(7, [_membership(issue)])

    assert [call.args[0] for call in client.remove_board_member.call_args_list] == ["found-reviews", "found-instr"]


def test_a_refused_removal_still_deactivates_the_user(issue, caplog):
    client = MagicMock()
    client.remove_board_member.side_effect = PlankaAPIError("Planka API 404: not found")
    with patch(BUILD_CLIENT, return_value=client):
        withdraw_planka_access_for_deleted_account(7, [_membership(issue, membership_id="m-1")])

    client.deactivate_user.assert_called_once_with("u-1")
    assert "deleted user 7" in caplog.text


def test_unconfigured_planka_is_left_alone(issue, caplog):
    with patch(BUILD_CLIENT, side_effect=PlankaAPIError("Planka is not configured.")):
        withdraw_planka_access_for_deleted_account(7, [_membership(issue)])
    assert "deleted user 7" in caplog.text


def test_deactivate_user_sets_only_the_deactivated_flag():
    client = PlankaClient(base_url="https://planka.test", api_key="key")
    with patch.object(client, "_request", return_value={"item": {"id": "u-1"}}) as request:
        client.deactivate_user("u-1")
    request.assert_called_once_with("PATCH", "/users/u-1", json={"isDeactivated": True})
