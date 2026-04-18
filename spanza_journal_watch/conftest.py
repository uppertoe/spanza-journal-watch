import pytest

from spanza_journal_watch.users.models import User
from spanza_journal_watch.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir):
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory()


_TEST_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@pytest.fixture
def client(client):
    """
    Default the test client to a realistic browser UA + sec-fetch-* headers so
    analytics middleware doesn't classify every test request as a bot and drop
    events. Tests that care about bot-UA behaviour can override with
    HTTP_USER_AGENT= (or omit sec-fetch for strict-event checks) on the call.
    """
    client.defaults.setdefault("HTTP_USER_AGENT", _TEST_BROWSER_USER_AGENT)
    client.defaults.setdefault("HTTP_SEC_FETCH_MODE", "navigate")
    client.defaults.setdefault("HTTP_SEC_FETCH_SITE", "same-origin")
    return client
