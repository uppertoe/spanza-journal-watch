import pytest
from django.conf import settings
from django.urls import reverse

from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_admin_login_sends_visitors_to_the_site_sign_in(client):
    response = client.get(reverse("admin:login"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


def test_admin_login_refuses_signed_in_non_staff(client):
    client.force_login(UserFactory(is_staff=False))
    assert client.get(reverse("admin:login")).status_code == 403


def test_staff_session_expires_sooner_than_a_reader_session(client):
    from django.contrib.auth.signals import user_logged_in
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.session = client.session
    staff = UserFactory(is_staff=True)
    user_logged_in.send(sender=staff.__class__, request=request, user=staff)
    assert request.session.get_expiry_age() == settings.STAFF_SESSION_COOKIE_AGE

    request.session = client.session
    reader = UserFactory(is_staff=False)
    user_logged_in.send(sender=reader.__class__, request=request, user=reader)
    assert request.session.get_expiry_age() == settings.SESSION_COOKIE_AGE
