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


def _signed_in_staff(email="editor@example.org"):
    from allauth.account.models import EmailAddress

    staff = UserFactory(email=email, is_staff=True)
    staff.set_password("Correct-Horse-9")
    staff.save()
    EmailAddress.objects.create(user=staff, email=staff.email, verified=True, primary=True)
    return staff


def test_remembered_staff_sign_in_lasts_a_week(client):
    staff = _signed_in_staff()
    response = client.post(
        reverse("account_login"), {"login": staff.email, "password": "Correct-Horse-9", "remember": "on"}
    )
    assert response.status_code == 302
    assert client.session.get_expiry_age() == settings.STAFF_SESSION_IDLE_AGE
    assert response.cookies[settings.SESSION_COOKIE_NAME]["max-age"] == settings.STAFF_SESSION_IDLE_AGE


def test_unremembered_staff_sign_in_ends_with_the_browser(client):
    staff = _signed_in_staff()
    client.post(reverse("account_login"), {"login": staff.email, "password": "Correct-Horse-9"})
    assert client.session.get_expire_at_browser_close()


def test_a_visit_renews_the_staff_week(client):
    client.force_login(_signed_in_staff())
    session = client.session
    session.set_expiry(60 * 60)  # an hour left
    session.save()

    client.get("/")

    assert client.session.get_expiry_age() == settings.STAFF_SESSION_IDLE_AGE


def test_recent_visits_do_not_rewrite_the_session(client):
    from spanza_journal_watch.utils.middleware import STAFF_SESSION_RENEW_INTERVAL

    client.force_login(_signed_in_staff())
    session = client.session
    session.set_expiry(settings.STAFF_SESSION_IDLE_AGE - STAFF_SESSION_RENEW_INTERVAL + 60)
    session.save()
    before = client.session.get_expiry_date()

    client.get("/")

    assert abs((client.session.get_expiry_date() - before).total_seconds()) < 2


def test_reader_sessions_are_left_at_the_default_length(client):
    client.force_login(UserFactory(is_staff=False))
    client.get("/")
    assert client.session.get_expiry_age() == settings.SESSION_COOKIE_AGE
