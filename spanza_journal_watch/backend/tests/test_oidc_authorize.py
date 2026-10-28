import pytest
from django.contrib.auth.models import Permission

from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

AUTHORIZE_URL = "/o/authorize/?response_type=code&client_id=planka&scope=openid"


def test_reader_account_cannot_authorise_planka(client):
    client.force_login(UserFactory(is_staff=False))
    assert client.get(AUTHORIZE_URL).status_code == 403


def test_contributor_reaches_the_authorize_endpoint(client):
    user = UserFactory(is_staff=False)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="submissions", codename="invited_contributor")
    )
    client.force_login(user)
    # The unknown client is refused by DOT itself, not by the role check
    assert client.get(AUTHORIZE_URL).status_code != 403


def test_signed_out_visitor_is_sent_to_sign_in(client):
    response = client.get(AUTHORIZE_URL)
    assert response.status_code == 302
