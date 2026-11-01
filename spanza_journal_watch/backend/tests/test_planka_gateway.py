"""The Planka sign-in gateway: Caddy's forward_auth check and the entry page."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from spanza_journal_watch.backend.planka_gateway import planka_groups, safe_planka_path
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHECK_URL = "/planka/check"
ENTRY_URL = "/planka/"


def _grant(user, codename):
    user.user_permissions.add(Permission.objects.get(content_type__app_label="submissions", codename=codename))
    return user


def _contributor():
    return _grant(UserFactory(is_staff=False), "invited_contributor")


def _chief_editor():
    return _grant(UserFactory(is_staff=True), "chief_editor")


@pytest.fixture(autouse=True)
def _gateway_settings(settings):
    settings.SITE_URL = "https://journalwatch.test"
    settings.PLANKA_EXTERNAL_URL = "https://planka.journalwatch.test"
    settings.PLANKA_GATEWAY_ADMIN_GROUP = "planka-admins"
    settings.PLANKA_GATEWAY_USER_GROUP = "planka-users"


# ---------------------------------------------------------------------------
# The check Caddy makes before every Planka request
# ---------------------------------------------------------------------------


def test_signed_out_visitor_is_sent_to_the_entry_page_with_the_planka_path(client):
    response = client.get(CHECK_URL, headers={"X-Forwarded-Uri": "/boards/42?x=1"})
    assert response.status_code == 302
    assert response["Location"] == "https://journalwatch.test/planka/?to=%2Fboards%2F42%3Fx%3D1"


def test_signed_out_visitor_at_the_planka_root_gets_a_plain_entry_url(client):
    response = client.get(CHECK_URL, headers={"X-Forwarded-Uri": "/"})
    assert response["Location"] == "https://journalwatch.test/planka/"


def test_reader_is_sent_to_the_entry_page_rather_than_let_through(client):
    client.force_login(UserFactory(is_staff=False))
    response = client.get(CHECK_URL)
    assert response.status_code == 302
    assert response["Location"].startswith("https://journalwatch.test/planka/")
    assert "Remote-Email" not in response


def test_contributor_is_let_through_as_a_board_user(client):
    user = _contributor()
    client.force_login(user)
    response = client.get(CHECK_URL)
    assert response.status_code == 204
    assert response["Remote-Email"] == user.email
    assert response["Remote-User"] == user.email
    assert response["Remote-Groups"] == "planka-users"
    assert "no-store" in response["Cache-Control"]


def test_staff_without_chief_editor_are_board_users(client):
    user = UserFactory(is_staff=True)
    client.force_login(user)
    assert client.get(CHECK_URL)["Remote-Groups"] == "planka-users"


def test_chief_editor_is_let_through_as_an_administrator(client):
    user = _chief_editor()
    client.force_login(user)
    response = client.get(CHECK_URL)
    assert response.status_code == 204
    assert response["Remote-Groups"] == "planka-admins"


def test_superuser_is_an_administrator(client):
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    assert client.get(CHECK_URL)["Remote-Groups"] == "planka-admins"


def test_group_names_follow_settings(settings):
    settings.PLANKA_GATEWAY_ADMIN_GROUP = "editors-in-chief"
    assert planka_groups(_chief_editor()) == ["editors-in-chief"]


def test_check_accepts_only_get(client):
    client.force_login(_contributor())
    assert client.post(CHECK_URL).status_code == 405


# ---------------------------------------------------------------------------
# The entry page on this site
# ---------------------------------------------------------------------------


def test_signed_out_visitor_to_the_entry_page_is_sent_to_sign_in_and_back(client):
    response = client.get(ENTRY_URL + "?to=/boards/42")
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))
    assert "next=/planka/%3Fto%3D/boards/42" in response["Location"]


def test_contributor_is_sent_on_to_planka(client):
    client.force_login(_contributor())
    response = client.get(ENTRY_URL + "?to=/boards/42")
    assert response.status_code == 302
    assert response["Location"] == "https://planka.journalwatch.test/boards/42"


def test_reader_sees_the_refusal_page(client):
    client.force_login(UserFactory(is_staff=False))
    response = client.get(ENTRY_URL)
    assert response.status_code == 403
    assert b"not set up for the editorial boards" in response.content


def test_entry_page_without_a_planka_url_says_the_boards_are_unavailable(client, settings):
    settings.PLANKA_EXTERNAL_URL = ""
    client.force_login(_contributor())
    response = client.get(ENTRY_URL)
    assert response.status_code == 503
    assert b"not available on this site at the moment" in response.content


# ---------------------------------------------------------------------------
# Only a path on the Planka host is followed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", None, "https://evil.example/", "//evil.example/", "/\\evil.example", "boards/1", "/a\nb", "/" + "x" * 2001],
)
def test_unsafe_return_paths_fall_back_to_the_root(value):
    assert safe_planka_path(value) == "/"


def test_a_board_path_with_a_query_is_kept():
    assert safe_planka_path("/boards/42?card=7") == "/boards/42?card=7"


def test_entry_page_ignores_an_offsite_return_target(client):
    client.force_login(_contributor())
    response = client.get(ENTRY_URL + "?to=https://evil.example/")
    assert response["Location"] == "https://planka.journalwatch.test/"
