from django.test import RequestFactory

from spanza_journal_watch.users.adapters import AccountAdapter


def test_client_ip_comes_from_the_forwarded_header(settings):
    settings.CLIENT_IP_HEADER = "X-Client-IP"
    request = RequestFactory().get("/", HTTP_X_CLIENT_IP="203.0.113.7", REMOTE_ADDR="172.18.0.5")
    assert AccountAdapter(request).get_client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_to_the_connecting_address(settings):
    settings.CLIENT_IP_HEADER = "X-Client-IP"
    request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.5")
    assert AccountAdapter(request).get_client_ip(request) == "172.18.0.5"
