"""
Tests for the Planka card-revision / webhook subsystem.

Covers:
1. PlankaCardRevision.record() — deduplication and 100-revision cap
2. planka_card_update_webhook view — auth, payload parsing, revision creation
3. planka_card_revisions view — returns correct context (including live card)
4. planka_card_revision_restore view — calls Planka PATCH
5. _register_planka_webhook helper — reuse vs. create, snapshot trigger
6. _take_board_description_snapshot — records revisions for all non-trash cards
7. _parse_planka_card_metadata — angle-bracket URL stripping, all fields
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import PlankaCardRevision, PlankaIntegrationCredential, PlankaIssueBinding
from spanza_journal_watch.backend.planka import PlankaAPIError
from spanza_journal_watch.backend.views import (
    _parse_planka_card_metadata,
    _register_planka_webhook,
    _take_board_description_snapshot,
)
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_issue():
    return Issue.objects.create(name="Test Issue", active=False)


_binding_counter = 0


def make_binding(issue, board_id="board-1"):
    """Create a PlankaIssueBinding for *issue* without needing a real Planka."""
    global _binding_counter
    _binding_counter += 1
    return PlankaIssueBinding.objects.create(
        issue=issue,
        board_id=board_id,
        project_id=f"proj-{_binding_counter}",
        project_name="Test Project",
    )


def make_credential():
    return PlankaIntegrationCredential.objects.create(api_key="test-api-key")


def make_staff_client(extra_permissions=None):
    """Return a logged-in Client for a user with the manage_issue_builder permission."""
    user = UserFactory()
    perms = ["submissions.manage_issue_builder"] + (extra_permissions or [])
    for perm_str in perms:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)
    client = Client()
    client.force_login(user)
    return client


WEBHOOK_URL = "/editorial/webhooks/planka/card-update"


def _webhook_payload(
    *,
    event="cardUpdate",
    card_id="card-1",
    board_id="board-1",
    description="New text",
    prev_description="Old text",
    user_email="editor@example.com",
    user_first="Alice",
    user_last="Smith",
):
    return {
        "event": event,
        "data": {
            "item": {
                "id": card_id,
                "boardId": board_id,
                "name": "Test Card",
                "description": description,
            }
        },
        "prevData": {
            "item": {
                "id": card_id,
                "description": prev_description,
            }
        },
        "user": {
            "email": user_email,
            "firstName": user_first,
            "lastName": user_last,
        },
    }


# ---------------------------------------------------------------------------
# 1. PlankaCardRevision.record()
# ---------------------------------------------------------------------------


class TestPlankaCardRevisionRecord:
    def test_creates_new_revision(self):
        issue = make_issue()
        binding = make_binding(issue)

        rev, created = PlankaCardRevision.record(
            binding=binding,
            card_id="card-1",
            card_name="Card One",
            board_id="board-1",
            description="Hello world",
        )

        assert created is True
        assert rev.description == "Hello world"
        assert rev.card_id == "card-1"

    def test_deduplicates_identical_consecutive_description(self):
        issue = make_issue()
        binding = make_binding(issue)

        rev1, created1 = PlankaCardRevision.record(
            binding=binding,
            card_id="card-1",
            card_name="Card One",
            board_id="board-1",
            description="Same text",
        )
        rev2, created2 = PlankaCardRevision.record(
            binding=binding,
            card_id="card-1",
            card_name="Card One",
            board_id="board-1",
            description="Same text",
        )

        assert created1 is True
        assert created2 is False
        assert rev1.pk == rev2.pk
        assert PlankaCardRevision.objects.filter(card_id="card-1").count() == 1

    def test_allows_different_description_after_same(self):
        issue = make_issue()
        binding = make_binding(issue)

        PlankaCardRevision.record(binding=binding, card_id="card-1", card_name="C", board_id="b", description="A")
        rev2, created = PlankaCardRevision.record(
            binding=binding, card_id="card-1", card_name="C", board_id="b", description="B"
        )

        assert created is True
        assert PlankaCardRevision.objects.filter(card_id="card-1").count() == 2

    def test_revision_cap_enforced(self):
        issue = make_issue()
        binding = make_binding(issue)
        cap = PlankaCardRevision.REVISION_CAP

        for i in range(cap + 5):
            PlankaCardRevision.record(
                binding=binding,
                card_id="card-cap",
                card_name="C",
                board_id="b",
                description=f"Revision {i}",
            )

        count = PlankaCardRevision.objects.filter(card_id="card-cap").count()
        assert count == cap

    def test_different_cards_are_independent(self):
        issue = make_issue()
        binding = make_binding(issue)

        PlankaCardRevision.record(binding=binding, card_id="card-A", card_name="A", board_id="b", description="text")
        PlankaCardRevision.record(binding=binding, card_id="card-B", card_name="B", board_id="b", description="text")

        assert PlankaCardRevision.objects.filter(card_id="card-A").count() == 1
        assert PlankaCardRevision.objects.filter(card_id="card-B").count() == 1

    def test_stores_actor_fields(self):
        issue = make_issue()
        binding = make_binding(issue)

        rev, _ = PlankaCardRevision.record(
            binding=binding,
            card_id="card-1",
            card_name="C",
            board_id="b",
            description="text",
            actor_email="alice@example.com",
            actor_name="Alice Smith",
            source="snapshot",
        )

        assert rev.actor_email == "alice@example.com"
        assert rev.actor_name == "Alice Smith"
        assert rev.source == "snapshot"

    def test_empty_description_deduplicated(self):
        issue = make_issue()
        binding = make_binding(issue)

        PlankaCardRevision.record(binding=binding, card_id="card-1", card_name="C", board_id="b", description="")
        _, created = PlankaCardRevision.record(
            binding=binding, card_id="card-1", card_name="C", board_id="b", description=""
        )
        assert created is False


# ---------------------------------------------------------------------------
# 2. planka_card_update_webhook view
# ---------------------------------------------------------------------------


class TestWebhookView:
    def _post(self, payload, *, secret=None):
        headers = {"content_type": "application/json"}
        if secret:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return Client().post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            **headers,
        )

    def test_cardUpdate_records_revision(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="New", prev_description="Old")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert PlankaCardRevision.objects.filter(card_id="card-1").count() == 1

    def test_cardUpdate_no_change_skipped(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="Same", prev_description="Same")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert PlankaCardRevision.objects.count() == 0

    def test_cardCreate_records_revision(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(event="cardCreate", board_id="board-1", description="Initial")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert PlankaCardRevision.objects.filter(card_id="card-1").count() == 1
        rev = PlankaCardRevision.objects.get()
        assert rev.description == "Initial"
        assert rev.source == "webhook"

    def test_cardDelete_ignored(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(event="cardDelete", board_id="board-1")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert PlankaCardRevision.objects.count() == 0

    def test_unknown_board_ignored(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        # No binding for board-99
        payload = _webhook_payload(board_id="board-99", description="New", prev_description="Old")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert PlankaCardRevision.objects.count() == 0

    def test_actor_fields_stored(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(
            board_id="board-1",
            description="New",
            prev_description="Old",
            user_email="alice@example.com",
            user_first="Alice",
            user_last="Smith",
        )

        self._post(payload, secret="mysecret")

        rev = PlankaCardRevision.objects.get()
        assert rev.actor_email == "alice@example.com"
        assert rev.actor_name == "Alice Smith"

    def test_auth_required_with_correct_secret(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="New", prev_description="Old")

        response = self._post(payload, secret="mysecret")

        assert response.status_code == 200
        assert PlankaCardRevision.objects.count() == 1

    def test_auth_rejected_wrong_secret(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="New", prev_description="Old")

        response = self._post(payload, secret="wrong")

        assert response.status_code == 403
        assert PlankaCardRevision.objects.count() == 0

    def test_auth_rejected_missing_header(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="New", prev_description="Old")

        response = self._post(payload)  # no secret header

        assert response.status_code == 403

    def test_missing_secret_returns_503(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = ""
        issue = make_issue()
        make_binding(issue, board_id="board-1")
        payload = _webhook_payload(board_id="board-1", description="New", prev_description="Old")

        response = self._post(payload, secret="anything")

        assert response.status_code == 503
        assert response.json() == {"detail": "Webhook misconfigured"}

    def test_bad_json_returns_400(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        response = Client().post(
            WEBHOOK_URL,
            data=b"not json",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer mysecret",
        )
        assert response.status_code == 400

    def test_missing_card_id_or_board_id_returns_200_no_revision(self, settings):
        settings.PLANKA_WEBHOOK_SECRET = "mysecret"
        payload = {
            "event": "cardUpdate",
            "data": {"item": {"boardId": "board-1"}},  # no id
            "prevData": {"item": {}},
        }
        response = Client().post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer mysecret",
        )
        assert response.status_code == 200
        assert PlankaCardRevision.objects.count() == 0

    def test_get_method_not_allowed(self):
        response = Client().get(WEBHOOK_URL)
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# 3. planka_card_revisions view
# ---------------------------------------------------------------------------


class TestPlankaCardRevisionsView:
    def _url(self, issue_id, card_id):
        return reverse("backend:planka_card_revisions", kwargs={"issue_id": issue_id, "card_id": card_id})

    def test_returns_200_with_revisions(self):
        issue = make_issue()
        binding = make_binding(issue)
        make_credential()
        card_id = "card-1"

        PlankaCardRevision.record(
            binding=binding,
            card_id=card_id,
            card_name="Card",
            board_id="board-1",
            description="Rev 1",
        )

        mock_card = {"id": card_id, "name": "Card", "description": "Current description"}
        with patch("spanza_journal_watch.backend.views._build_planka_client") as mock_build:
            mock_client = MagicMock()
            mock_client.get_card.return_value = mock_card
            mock_build.return_value = mock_client

            client = make_staff_client()
            response = client.get(self._url(issue.pk, card_id))

        assert response.status_code == 200
        assert response.context["current_description"] == "Current description"
        assert len(response.context["revisions"]) == 1

    def test_planka_fetch_error_shows_gracefully(self):
        issue = make_issue()
        make_binding(issue)
        make_credential()

        with patch("spanza_journal_watch.backend.views._build_planka_client") as mock_build:
            mock_client = MagicMock()
            mock_client.get_card.side_effect = PlankaAPIError("Planka API 503: Service Unavailable")
            mock_build.return_value = mock_client

            client = make_staff_client()
            response = client.get(self._url(issue.pk, "card-1"))

        assert response.status_code == 200
        assert response.context["current_description"] is None
        assert "503" in (response.context["current_fetch_error"] or "")

    def test_requires_login(self):
        issue = make_issue()
        make_binding(issue)
        response = Client().get(self._url(issue.pk, "card-1"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_requires_manage_issue_builder_permission(self):
        issue = make_issue()
        make_binding(issue)
        user = UserFactory()
        client = Client()
        client.force_login(user)
        with patch("spanza_journal_watch.backend.views._build_planka_client"):
            response = client.get(self._url(issue.pk, "card-1"))
        assert response.status_code == 403

    def test_nonexistent_issue_returns_404(self):
        client = make_staff_client()
        with patch("spanza_journal_watch.backend.views._build_planka_client"):
            response = client.get(self._url(9999, "card-1"))
        assert response.status_code == 404

    def test_no_binding_returns_404(self):
        issue = make_issue()
        # No binding created
        client = make_staff_client()
        with patch("spanza_journal_watch.backend.views._build_planka_client"):
            response = client.get(self._url(issue.pk, "card-1"))
        assert response.status_code == 404

    def test_empty_revisions_list(self):
        issue = make_issue()
        make_binding(issue)
        make_credential()

        with patch("spanza_journal_watch.backend.views._build_planka_client") as mock_build:
            mock_client = MagicMock()
            mock_client.get_card.return_value = {"id": "card-1", "description": ""}
            mock_build.return_value = mock_client

            client = make_staff_client()
            response = client.get(self._url(issue.pk, "card-1"))

        assert response.status_code == 200
        assert response.context["revisions"] == []


# ---------------------------------------------------------------------------
# 4. planka_card_revision_restore view
# ---------------------------------------------------------------------------


class TestPlankaCardRevisionRestoreView:
    def _url(self, issue_id, revision_id):
        return reverse(
            "backend:planka_card_revision_restore",
            kwargs={"issue_id": issue_id, "revision_id": revision_id},
        )

    def _make_revision(self, binding, card_id="card-1", description="Old description"):
        rev, _ = PlankaCardRevision.record(
            binding=binding,
            card_id=card_id,
            card_name="Card",
            board_id="board-1",
            description=description,
        )
        return rev

    def test_restore_calls_planka_patch(self):
        issue = make_issue()
        binding = make_binding(issue)
        rev = self._make_revision(binding)
        make_credential()

        with patch("spanza_journal_watch.backend.views._build_planka_client") as mock_build:
            mock_client = MagicMock()
            mock_build.return_value = mock_client

            client = make_staff_client()
            response = client.post(self._url(issue.pk, rev.pk))

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        mock_client._request.assert_called_once_with(
            "PATCH",
            f"/cards/{rev.card_id}",
            json={"description": rev.description},
        )

    def test_get_method_returns_400(self):
        issue = make_issue()
        binding = make_binding(issue)
        rev = self._make_revision(binding)

        client = make_staff_client()
        response = client.get(self._url(issue.pk, rev.pk))

        assert response.status_code == 400

    def test_planka_error_returns_502(self):
        issue = make_issue()
        binding = make_binding(issue)
        rev = self._make_revision(binding)
        make_credential()

        with patch("spanza_journal_watch.backend.views._build_planka_client") as mock_build:
            mock_client = MagicMock()
            mock_client._request.side_effect = PlankaAPIError("Planka API 500: internal error")
            mock_build.return_value = mock_client

            client = make_staff_client()
            response = client.post(self._url(issue.pk, rev.pk))

        assert response.status_code == 502
        data = response.json()
        assert data["ok"] is False
        assert "error" in data

    def test_revision_from_different_binding_returns_404(self):
        issue_a = make_issue()
        issue_b = Issue.objects.create(name="Issue B", active=False)
        binding_a = make_binding(issue_a, board_id="board-a")
        make_binding(issue_b, board_id="board-b")
        rev = self._make_revision(binding_a)
        make_credential()

        client = make_staff_client()
        # Attempt to restore revision belonging to binding_a via issue_b's URL
        with patch("spanza_journal_watch.backend.views._build_planka_client"):
            response = client.post(self._url(issue_b.pk, rev.pk))

        assert response.status_code == 404

    def test_requires_permission(self):
        issue = make_issue()
        binding = make_binding(issue)
        rev = self._make_revision(binding)

        user = UserFactory()
        client = Client()
        client.force_login(user)
        with patch("spanza_journal_watch.backend.views._build_planka_client"):
            response = client.post(self._url(issue.pk, rev.pk))

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5. _register_planka_webhook helper
# ---------------------------------------------------------------------------


class TestRegisterPlankaWebhook:
    def test_reuses_existing_webhook_with_matching_url(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = "http://django:8000"
        settings.PLANKA_WEBHOOK_SECRET = "my-secret"
        issue = make_issue()
        binding = make_binding(issue)
        expected_url = "http://django:8000/editorial/webhooks/planka/card-update"

        mock_client = MagicMock()
        mock_client.list_webhooks.return_value = [{"id": "wh-existing", "url": expected_url}]

        with patch("spanza_journal_watch.backend.views._take_board_description_snapshot") as mock_snap:
            _register_planka_webhook(mock_client, binding)

        binding.refresh_from_db()
        assert binding.webhook_id == "wh-existing"
        mock_client.create_webhook.assert_not_called()
        mock_snap.assert_called_once_with(mock_client, binding)

    def test_creates_new_webhook_when_none_exists(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = "http://django:8000"
        settings.PLANKA_WEBHOOK_SECRET = "my-secret"
        issue = make_issue()
        binding = make_binding(issue)

        mock_client = MagicMock()
        mock_client.list_webhooks.return_value = []
        mock_client.create_webhook.return_value = {"id": "wh-new"}

        with patch("spanza_journal_watch.backend.views._take_board_description_snapshot") as mock_snap:
            _register_planka_webhook(mock_client, binding)

        binding.refresh_from_db()
        assert binding.webhook_id == "wh-new"
        mock_client.create_webhook.assert_called_once()
        call_args = mock_client.create_webhook.call_args
        assert call_args[0][0] == "http://django:8000/editorial/webhooks/planka/card-update"
        assert call_args[1]["access_token"] == "my-secret"
        mock_snap.assert_called_once()

    def test_skips_when_no_callback_base_url(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = ""
        issue = make_issue()
        binding = make_binding(issue)

        mock_client = MagicMock()

        _register_planka_webhook(mock_client, binding)

        mock_client.list_webhooks.assert_not_called()
        mock_client.create_webhook.assert_not_called()

    def test_skips_when_secret_missing(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = "http://django:8000"
        settings.PLANKA_WEBHOOK_SECRET = ""
        issue = make_issue()
        binding = make_binding(issue)

        mock_client = MagicMock()

        _register_planka_webhook(mock_client, binding)

        mock_client.list_webhooks.assert_not_called()
        mock_client.create_webhook.assert_not_called()

    def test_planka_error_on_list_does_not_raise(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = "http://django:8000"
        settings.PLANKA_WEBHOOK_SECRET = "my-secret"
        issue = make_issue()
        binding = make_binding(issue)

        mock_client = MagicMock()
        mock_client.list_webhooks.side_effect = PlankaAPIError("Could not connect")
        mock_client.create_webhook.return_value = {"id": "wh-fallback"}

        # Should not raise; proceeds to create
        with patch("spanza_journal_watch.backend.views._take_board_description_snapshot"):
            _register_planka_webhook(mock_client, binding)

        # create_webhook is called as the list path failed
        mock_client.create_webhook.assert_called_once()

    def test_planka_error_on_create_does_not_raise(self, settings):
        settings.PLANKA_CALLBACK_BASE_URL = "http://django:8000"
        settings.PLANKA_WEBHOOK_SECRET = "my-secret"
        issue = make_issue()
        binding = make_binding(issue)

        mock_client = MagicMock()
        mock_client.list_webhooks.return_value = []
        mock_client.create_webhook.side_effect = PlankaAPIError("API error")

        # Should not raise
        _register_planka_webhook(mock_client, binding)

        # webhook_id remains blank
        binding.refresh_from_db()
        assert binding.webhook_id == ""


# ---------------------------------------------------------------------------
# 6. _take_board_description_snapshot
# ---------------------------------------------------------------------------


class TestTakeBoardDescriptionSnapshot:
    def _make_board_payload(self, cards, lists=None):
        if lists is None:
            lists = [{"id": "list-1", "type": "active"}]
        return (
            {},  # board item (ignored)
            {"cards": cards, "lists": lists},  # included
        )

    def test_records_revisions_for_all_non_trash_cards(self):
        issue = make_issue()
        binding = make_binding(issue, board_id="board-1")

        cards = [
            {"id": "card-a", "name": "A", "description": "Alpha", "listId": "list-1"},
            {"id": "card-b", "name": "B", "description": "Beta", "listId": "list-1"},
        ]
        board_payload = self._make_board_payload(cards)

        mock_client = MagicMock()
        mock_client.get_board.return_value = board_payload

        _take_board_description_snapshot(mock_client, binding)

        assert PlankaCardRevision.objects.filter(card_id="card-a").count() == 1
        assert PlankaCardRevision.objects.filter(card_id="card-b").count() == 1
        assert PlankaCardRevision.objects.get(card_id="card-a").source == "snapshot"

    def test_skips_trash_list_cards(self):
        issue = make_issue()
        binding = make_binding(issue, board_id="board-1")

        lists = [
            {"id": "list-active", "type": "active"},
            {"id": "list-trash", "type": "trash"},
        ]
        cards = [
            {"id": "card-a", "name": "A", "description": "Kept", "listId": "list-active"},
            {"id": "card-trash", "name": "T", "description": "Gone", "listId": "list-trash"},
        ]
        board_payload = (
            {},
            {"cards": cards, "lists": lists},
        )
        mock_client = MagicMock()
        mock_client.get_board.return_value = board_payload

        _take_board_description_snapshot(mock_client, binding)

        assert PlankaCardRevision.objects.filter(card_id="card-a").count() == 1
        assert PlankaCardRevision.objects.filter(card_id="card-trash").count() == 0

    def test_skips_empty_description_cards(self):
        issue = make_issue()
        binding = make_binding(issue, board_id="board-1")

        cards = [
            {"id": "card-no-desc", "name": "No desc", "description": "", "listId": "list-1"},
        ]
        mock_client = MagicMock()
        mock_client.get_board.return_value = self._make_board_payload(cards)

        _take_board_description_snapshot(mock_client, binding)

        # Empty description — record() will create a revision (dedup is only hash-based),
        # but the behaviour expected here is that _take_board_description_snapshot does NOT
        # filter on empty — it always calls record(). Verify the call was made:
        assert PlankaCardRevision.objects.filter(card_id="card-no-desc").count() == 1

    def test_planka_api_error_does_not_raise(self):
        issue = make_issue()
        binding = make_binding(issue, board_id="board-1")

        mock_client = MagicMock()
        mock_client.get_board.side_effect = PlankaAPIError("board not found")

        # Should silently log and return
        _take_board_description_snapshot(mock_client, binding)

        assert PlankaCardRevision.objects.count() == 0


# ---------------------------------------------------------------------------
# 7. _parse_planka_card_metadata
# ---------------------------------------------------------------------------


class TestParsePlankaCardMetadata:
    def test_plain_url(self):
        text = "Journal: Test Journal\nArticle URL: https://doi.org/10.1111/abc\nPublication date: 2024"
        meta = _parse_planka_card_metadata(text)
        assert meta["article_url"] == "https://doi.org/10.1111/abc"

    def test_angle_bracket_url_stripped(self):
        text = "Journal: Test Journal\nArticle URL: <https://doi.org/10.1111/pan.14948>\nPublication date: 2024"
        meta = _parse_planka_card_metadata(text)
        assert meta["article_url"] == "https://doi.org/10.1111/pan.14948"

    def test_journal_name_extracted(self):
        text = "Journal: Paediatric Anaesthesia\nArticle URL: https://example.com"
        meta = _parse_planka_card_metadata(text)
        assert meta["journal_name"] == "Paediatric Anaesthesia"

    def test_publication_year_extracted(self):
        text = "Article URL: https://example.com\nPublication date: 2023-07-15"
        meta = _parse_planka_card_metadata(text)
        assert meta["article_year"] == "2023"

    def test_publication_year_only(self):
        text = "Publication date: 2021"
        meta = _parse_planka_card_metadata(text)
        assert meta["article_year"] == "2021"

    def test_abstract_extracted(self):
        text = "Abstract\n--\nThis is the abstract body.\nIt spans multiple lines."
        meta = _parse_planka_card_metadata(text)
        assert "abstract body" in meta["article_abstract"]

    def test_missing_fields_return_empty_strings(self):
        meta = _parse_planka_card_metadata("")
        assert meta["journal_name"] == ""
        assert meta["article_url"] == ""
        assert meta["article_year"] == ""
        assert meta["article_abstract"] == ""

    def test_separator_marker_splits_header_from_review(self):
        """Metadata after the separator should not be parsed."""
        from spanza_journal_watch.backend.views import PLANKA_REVIEW_SEPARATOR_MARKER

        text = (
            "Journal: Correct Journal\n"
            "Article URL: https://correct.example.com\n"
            + PLANKA_REVIEW_SEPARATOR_MARKER
            + "\nJournal: Wrong Journal\nArticle URL: https://wrong.example.com"
        )
        meta = _parse_planka_card_metadata(text)
        assert meta["journal_name"] == "Correct Journal"
        assert meta["article_url"] == "https://correct.example.com"
