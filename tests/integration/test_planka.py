"""
Integration tests for Planka API client against the local Planka instance.

These tests create real projects/boards/cards in the local Planka and clean up
after themselves. Requires the Planka service to be running:
    docker compose -f local.yml --profile planka up -d

Run: pytest -m integration tests/integration/test_planka.py
"""

import pytest

from spanza_journal_watch.backend.planka import PlankaAPIError

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


# ---------------------------------------------------------------------------
# 1. Authentication & bootstrap
# ---------------------------------------------------------------------------


class TestPlankaAuth:
    def test_bootstrap_returns_data(self, planka_client):
        data = planka_client.get_bootstrap()
        # Bootstrap should return oidc config or at least an empty dict
        assert isinstance(data, dict)

    def test_get_current_user(self, planka_client):
        user = planka_client.get_current_user()
        assert user.get("email") == "demo@demo.demo"
        assert user.get("name")

    def test_list_users(self, planka_client):
        users = planka_client.list_users()
        assert len(users) >= 1
        emails = [u.get("email") for u in users]
        assert "demo@demo.demo" in emails

    def test_find_user_by_email(self, planka_client):
        user = planka_client.find_user_by_email("demo@demo.demo")
        assert user is not None
        assert user["email"] == "demo@demo.demo"

    def test_find_user_by_email_not_found(self, planka_client):
        user = planka_client.find_user_by_email("nonexistent@example.com")
        assert user is None


# ---------------------------------------------------------------------------
# 2. Project CRUD
# ---------------------------------------------------------------------------


class TestPlankaProjectCRUD:
    def test_create_and_get_project(self, planka_client, planka_test_project):
        project_id = planka_test_project["id"]
        fetched = planka_client.get_project(project_id)
        assert fetched["id"] == project_id
        assert fetched["name"] == "__test_integration__"

    def test_update_project_name(self, planka_client, planka_test_project):
        project_id = planka_test_project["id"]
        updated = planka_client.update_project_name(project_id, "__test_renamed__")
        assert updated["name"] == "__test_renamed__"

    def test_make_project_shared(self, planka_client, planka_test_project):
        project_id = planka_test_project["id"]
        result = planka_client.make_project_shared(project_id)
        assert result.get("id") == project_id


# ---------------------------------------------------------------------------
# 3. Board + List + Card pipeline
# ---------------------------------------------------------------------------


class TestPlankaBoardPipeline:
    @pytest.fixture
    def board(self, planka_client, planka_test_project):
        return planka_client.create_board(planka_test_project["id"], name="Test Board")

    @pytest.fixture
    def list_(self, planka_client, board):
        return planka_client.create_list(board["id"], name="Test List", position=65536)

    def test_create_board(self, board):
        assert board.get("id")
        assert board["name"] == "Test Board"

    def test_get_board(self, planka_client, board):
        fetched, included = planka_client.get_board(board["id"])
        assert fetched["id"] == board["id"]
        assert isinstance(included, dict)

    def test_create_list(self, list_):
        assert list_.get("id")
        assert list_["name"] == "Test List"

    def test_update_list(self, planka_client, list_):
        updated = planka_client.update_list(list_["id"], name="Renamed List", color="berry-red")
        assert updated["name"] == "Renamed List"

    def test_create_card_with_description(self, planka_client, list_):
        card = planka_client.create_card(
            list_["id"],
            name="Test Card",
            description="Test description",
        )
        assert card.get("id")
        assert card["name"] == "Test Card"

    def test_create_card_without_description(self, planka_client, list_):
        card = planka_client.create_card(list_["id"], name="No Desc Card", position=131072)
        assert card.get("id")
        assert card["name"] == "No Desc Card"
        assert card.get("description") is None

    def test_get_card(self, planka_client, list_):
        card = planka_client.create_card(list_["id"], name="Fetch Me", description="desc")
        fetched = planka_client.get_card(card["id"])
        assert fetched["id"] == card["id"]
        assert fetched["name"] == "Fetch Me"

    def test_move_card_between_lists(self, planka_client, board, list_):
        card = planka_client.create_card(list_["id"], name="Move Me", description="desc")
        new_list = planka_client.create_list(board["id"], name="Target List", position=131072)
        moved = planka_client.move_card(card["id"], new_list["id"])
        assert moved.get("listId") == new_list["id"]

    def test_delete_card(self, planka_client, list_):
        card = planka_client.create_card(list_["id"], name="Delete Me", description="desc")
        assert planka_client.delete_card(card["id"]) is True
        with pytest.raises(PlankaAPIError, match="404"):
            planka_client.get_card(card["id"])


# ---------------------------------------------------------------------------
# 4. Labels
# ---------------------------------------------------------------------------


class TestPlankaLabels:
    @pytest.fixture
    def board(self, planka_client, planka_test_project):
        return planka_client.create_board(planka_test_project["id"], name="Label Board")

    def test_create_label(self, planka_client, board):
        label = planka_client.create_label(board["id"], name="Urgent", color="berry-red")
        assert label.get("id")
        assert label["name"] == "Urgent"

    def test_update_label(self, planka_client, board):
        label = planka_client.create_label(board["id"], name="Old Name")
        updated = planka_client.update_label(label["id"], name="New Name", color="lagoon-blue")
        assert updated["name"] == "New Name"

    def test_add_label_to_card(self, planka_client, board):
        list_ = planka_client.create_list(board["id"], name="Label List", position=65536)
        card = planka_client.create_card(list_["id"], name="Labeled Card", description="desc")
        label = planka_client.create_label(board["id"], name="Tag")
        result = planka_client.add_label_to_card(card["id"], label["id"])
        assert result.get("id")


# ---------------------------------------------------------------------------
# 5. Custom fields
# ---------------------------------------------------------------------------


class TestPlankaCustomFields:
    @pytest.fixture
    def board(self, planka_client, planka_test_project):
        return planka_client.create_board(planka_test_project["id"], name="CustomField Board")

    def test_create_custom_field_group(self, planka_client, board):
        group = planka_client.create_custom_field_group(board["id"], name="Test Group")
        assert group.get("id")
        assert group["name"] == "Test Group"

    def test_create_custom_field(self, planka_client, board):
        group = planka_client.create_custom_field_group(board["id"], name="CF Group")
        field = planka_client.create_custom_field(group["id"], name="DOI", position=65536)
        assert field.get("id")
        assert field["name"] == "DOI"

    def test_set_custom_field_value(self, planka_client, board):
        list_ = planka_client.create_list(board["id"], name="CF List", position=65536)
        card = planka_client.create_card(list_["id"], name="CF Card", description="desc")
        group = planka_client.create_custom_field_group(board["id"], name="Values Group")
        field = planka_client.create_custom_field(group["id"], name="PMID", position=65536)
        result = planka_client.create_custom_field_value(card["id"], group["id"], field["id"], content="12345678")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. Webhooks
# ---------------------------------------------------------------------------


class TestPlankaWebhooks:
    def test_create_and_delete_webhook(self, planka_client):
        webhook = planka_client.create_webhook(
            "http://localhost:8000/planka/webhook/",
            name="__test_webhook__",
        )
        assert webhook.get("id")
        assert webhook["name"] == "__test_webhook__"

        # Clean up
        assert planka_client.delete_webhook(webhook["id"]) is True

    def test_list_webhooks(self, planka_client):
        webhooks = planka_client.list_webhooks()
        assert isinstance(webhooks, list)

    def test_delete_nonexistent_webhook_returns_false(self, planka_client):
        # Create then delete, then delete again — second delete should return False
        webhook = planka_client.create_webhook(
            "http://localhost:8000/planka/webhook/",
            name="__test_double_delete__",
        )
        planka_client.delete_webhook(webhook["id"])
        result = planka_client.delete_webhook(webhook["id"])
        assert result is False
