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


def _signed_in_staff(email="editor@example.org"):
    from allauth.account.models import EmailAddress

    staff = UserFactory(email=email, is_staff=True)
    staff.set_password("Correct-Horse-9")
    staff.save()
    EmailAddress.objects.create(user=staff, email=staff.email, verified=True, primary=True)
    return staff


def test_password_sign_in_keeps_the_staff_session_limit(client):
    staff = _signed_in_staff()
    response = client.post(
        reverse("account_login"), {"login": staff.email, "password": "Correct-Horse-9", "remember": "on"}
    )
    assert response.status_code == 302
    assert client.session.get_expiry_age() <= settings.STAFF_SESSION_COOKIE_AGE
    assert response.cookies[settings.SESSION_COOKIE_NAME]["max-age"] <= settings.STAFF_SESSION_COOKIE_AGE


def test_staff_session_is_signed_out_once_the_limit_has_passed(client):
    import time

    from spanza_journal_watch.utils.middleware import STAFF_SIGNED_IN_AT_SESSION_KEY

    staff = _signed_in_staff()
    client.force_login(staff)
    session = client.session
    session[STAFF_SIGNED_IN_AT_SESSION_KEY] = time.time() - settings.STAFF_SESSION_COOKIE_AGE - 60
    session.set_expiry(None)
    session.save()

    client.get("/")

    assert "_auth_user_id" not in client.session


def test_later_requests_do_not_extend_the_staff_session(client):
    from spanza_journal_watch.utils.middleware import STAFF_SIGNED_IN_AT_SESSION_KEY

    client.force_login(_signed_in_staff())
    client.get("/")
    signed_in_at = client.session[STAFF_SIGNED_IN_AT_SESSION_KEY]
    first_expiry = client.session.get_expiry_date()
    client.get("/")
    assert client.session[STAFF_SIGNED_IN_AT_SESSION_KEY] == signed_in_at
    assert abs((client.session.get_expiry_date() - first_expiry).total_seconds()) < 2


def test_reader_sessions_are_left_at_the_default_length(client):
    client.force_login(UserFactory(is_staff=False))
    client.get("/")
    assert client.session.get_expiry_age() == settings.SESSION_COOKIE_AGE
