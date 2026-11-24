import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.analytics.utils import expected_chromium_major
from spanza_journal_watch.users.signup_guard import ipv6_prefix

pytestmark = pytest.mark.django_db

User = get_user_model()


def _chrome(major):
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


# Headers a current Chrome sends with a sign-up post.
REAL_CHROME = {
    "HTTP_USER_AGENT": _chrome(expected_chromium_major()),
    "HTTP_ACCEPT_LANGUAGE": "en-GB,en;q=0.9",
    "HTTP_PRIORITY": "u=0, i",
}

# Headers the fleet of 2026-11-23 sent.
FLEET = {
    "HTTP_USER_AGENT": _chrome(134),
    "HTTP_ACCEPT_LANGUAGE": "en-US, en; q=0.9",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    mail.outbox.clear()
    yield
    cache.clear()


def _post(headers, email, ip="2001:db8:1:1::1", url_name="account_signup", client=None):
    client = client or Client()
    response = client.post(reverse(url_name), {"email": email}, REMOTE_ADDR=ip, HTTP_X_FORWARDED_FOR=ip, **headers)
    return client, response


class TestScriptedHeaders:
    def test_a_current_chrome_signs_up(self):
        _post(REAL_CHROME, "reader@example.org")
        assert User.objects.filter(email="reader@example.org").exists()
        assert len(mail.outbox) == 1

    def test_the_spaced_accept_language_is_refused_silently(self):
        client, response = _post(FLEET, "victim@example.org")
        assert response.url == reverse("account_signup")
        assert not User.objects.filter(email="victim@example.org").exists()
        assert not mail.outbox
        page = client.get(response.url).content.decode()
        assert "We have not been able" not in page

    def test_the_spaced_accept_language_is_refused_through_start(self):
        _post(FLEET, "victim@example.org", url_name="users:start")
        assert not User.objects.filter(email="victim@example.org").exists()
        assert not mail.outbox

    def test_a_stale_chrome_without_priority_is_refused_with_a_message(self):
        headers = {**REAL_CHROME, "HTTP_USER_AGENT": _chrome(expected_chromium_major() - 20)}
        del headers["HTTP_PRIORITY"]
        client, response = _post(headers, "old@example.org")
        assert not User.objects.filter(email="old@example.org").exists()
        assert not mail.outbox
        page = client.get(response.url).content.decode()
        assert "We have not been able to create your account from this browser." in page

    def test_a_stale_chrome_that_sends_priority_signs_up(self):
        headers = {**REAL_CHROME, "HTTP_USER_AGENT": _chrome(expected_chromium_major() - 20)}
        _post(headers, "managed@example.org")
        assert User.objects.filter(email="managed@example.org").exists()

    def test_a_slightly_older_chrome_without_priority_signs_up(self):
        headers = {**REAL_CHROME, "HTTP_USER_AGENT": _chrome(expected_chromium_major() - 10)}
        del headers["HTTP_PRIORITY"]
        _post(headers, "proxy@example.org")
        assert User.objects.filter(email="proxy@example.org").exists()

    def test_safari_and_firefox_need_no_priority(self):
        safari = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        )
        _post({"HTTP_USER_AGENT": safari, "HTTP_ACCEPT_LANGUAGE": "en-AU"}, "safari@example.org")
        assert User.objects.filter(email="safari@example.org").exists()

    def test_an_invited_contributor_is_not_checked(self, monkeypatch):
        monkeypatch.setattr(
            "spanza_journal_watch.users.adapters.pending_invite_email", lambda request: "inv@example.org"
        )
        _post(FLEET, "inv@example.org")
        assert User.objects.filter(email="inv@example.org").exists()


class TestIpv6PrefixCap:
    def test_one_account_per_prefix_per_day(self):
        _post(REAL_CHROME, "first@example.org", ip="2001:db8:7001:4ee5:76a9::1")
        client, response = _post(REAL_CHROME, "second@example.org", ip="2001:db8:7001:4ee5:e021::2")
        assert not User.objects.filter(email="second@example.org").exists()
        assert len(mail.outbox) == 1
        page = client.get(response.url).content.decode()
        assert "We are not able to create another account from this connection today." in page

    def test_a_different_prefix_is_not_affected(self):
        _post(REAL_CHROME, "first@example.org", ip="2001:db8:7001:4ee5::1")
        _post(REAL_CHROME, "second@example.org", ip="2001:db8:7001:4ee6::1")
        assert User.objects.filter(email="second@example.org").exists()

    def test_ipv4_is_not_capped_by_address(self):
        _post(REAL_CHROME, "first@example.org", ip="203.0.113.7")
        _post(REAL_CHROME, "second@example.org", ip="203.0.113.7")
        assert User.objects.filter(email="second@example.org").exists()

    def test_prefix_of(self):
        assert ipv6_prefix("2001:19f0:7001:4ee5:76a9:3ccd:903b:a5d0") == "2001:19f0:7001:4ee5::/64"
        assert ipv6_prefix("203.0.113.7") is None
        assert ipv6_prefix("::ffff:203.0.113.7") is None
        assert ipv6_prefix("not an address") is None
