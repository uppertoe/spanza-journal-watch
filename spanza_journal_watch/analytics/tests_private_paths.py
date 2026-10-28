"""Confirmation, invite and account paths are kept out of analytics."""

import importlib
import json

import pytest
from django.apps import apps
from django.urls import reverse

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.analytics.utils import scrub_private_metadata, scrub_private_url

pytestmark = pytest.mark.django_db

SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def _beacon(client, payload):
    return client.post(
        reverse("analytics:track_event"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_USER_AGENT=SAFARI_UA,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/newsletter/confirm/abc123", "/newsletter/"),
        ("/users/42/", "/users/"),
        ("/accounts/confirm-email/key/", "/accounts/"),
        ("/invites/issue/token/", "/invites/"),
        ("https://jw.example/newsletter/unsubscribe/tok", "/newsletter/"),
        ("/reviews/some-review/?t=secret&utm_source=newsletter", "/reviews/some-review/?utm_source=newsletter"),
        ("/reviews/some-review/", "/reviews/some-review/"),
        ("anaesthesia", "anaesthesia"),
        (7, 7),
    ],
)
def test_scrub_private_url(value, expected):
    assert scrub_private_url(value) == expected


def test_scrub_private_metadata_reaches_nested_values():
    assert scrub_private_metadata({"path": "/users/1/", "links": ["/accounts/x/", "/"], "page": "home"}) == {
        "path": "/users/",
        "links": ["/accounts/", "/"],
        "page": "home",
    }


def test_record_event_keeps_only_the_prefix():
    event = AnalyticsEvent.record_event(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        landing_page="/newsletter/confirm/abc123",
        metadata={"destination_url": "https://jw.example/invites/issue/tok/", "path": "/"},
    )
    assert event.landing_page == "/newsletter/"
    assert event.metadata == {"destination_url": "/invites/", "path": "/"}


def test_page_load_on_a_private_page_is_not_recorded(client):
    response = _beacon(
        client,
        {
            "event_type": "page_visit",
            "source": "page_load",
            "metadata": {"path": "/newsletter/confirm/abc123"},
            "page_url": "https://jw.example/newsletter/confirm/abc123",
        },
    )
    assert response.status_code == 200
    assert not AnalyticsEvent.objects.exists()


def test_beacon_landing_page_and_metadata_are_scrubbed(client):
    _beacon(
        client,
        {
            "event_type": "page_visit",
            "source": "page_load",
            "metadata": {"path": "/", "landing_page": "/accounts/confirm-email/key/"},
            "landing_page": "/users/42/",
            "page_url": "https://jw.example/",
        },
    )
    event = AnalyticsEvent.objects.get()
    assert event.landing_page == "/users/"
    assert event.metadata["landing_page"] == "/accounts/"


def test_migration_scrubs_existing_rows():
    private = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        landing_page="/newsletter/confirm/abc123",
        metadata={"path": "/users/42/", "destination_url": "/reviews/x/?t=secret"},
    )
    public = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        landing_page="/reviews/x/",
        metadata={"path": "/reviews/x/"},
    )
    migration = importlib.import_module("spanza_journal_watch.analytics.migrations.0031_scrub_private_paths")

    migration.scrub_private_paths(apps, None)

    private.refresh_from_db()
    public.refresh_from_db()
    assert private.landing_page == "/newsletter/"
    assert private.metadata == {"path": "/users/", "destination_url": "/reviews/x/"}
    assert public.landing_page == "/reviews/x/"
    assert public.metadata == {"path": "/reviews/x/"}
