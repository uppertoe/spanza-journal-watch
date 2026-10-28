from unittest.mock import patch

import pytest
from django.core.cache import cache

from spanza_journal_watch.newsletter.confirmation import VISITOR_HOURLY_LIMIT

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _subscribe(client, email, **extra):
    data = {"email": email, "source": "drawer", **extra}
    return client.post("/newsletter/subscribe?source=drawer", data, HTTP_HX_REQUEST="true")


def test_filled_trap_field_sends_nothing_but_looks_the_same(client):
    with patch("spanza_journal_watch.newsletter.views.send_subscription_request_email") as task:
        response = _subscribe(client, "bot@example.com", jw_hp_extra="filled")
    assert response.status_code == 200
    assert "We have sent a confirmation link" in response.content.decode()
    task.delay.assert_not_called()


def test_one_visitor_is_limited_across_addresses(client):
    with patch("spanza_journal_watch.newsletter.views.send_subscription_request_email") as task:
        for n in range(VISITOR_HOURLY_LIMIT + 2):
            response = _subscribe(client, f"person{n}@example.com")
            assert response.status_code == 200
    assert task.delay.call_count == VISITOR_HOURLY_LIMIT


def test_refresh_tokens_expire():
    from django.conf import settings

    assert settings.OAUTH2_PROVIDER["REFRESH_TOKEN_EXPIRE_SECONDS"] == 30 * 24 * 60 * 60
