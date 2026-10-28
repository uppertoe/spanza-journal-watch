import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_privacy_policy_page_renders(client):
    response = client.get(reverse("privacy"))
    assert response.status_code == 200
    assert "journalwatch@spanza.org.au" in response.content.decode()


@pytest.mark.parametrize("url_name", ["home", "account_login"])
def test_pages_link_to_the_privacy_policy(client, url_name):
    assert f'href="{reverse("privacy")}"' in client.get(reverse(url_name)).content.decode()
