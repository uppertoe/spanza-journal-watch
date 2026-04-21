from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from spanza_journal_watch.layout.models import Homepage
from spanza_journal_watch.submissions.models import Issue, MeshTagMapping, Tag


@pytest.fixture(scope="session")
def regression_baseline(django_db_setup, django_db_blocker):
    fixture_name = "regression_baseline.json"
    fixture_path = Path(settings.BASE_DIR) / "spanza_journal_watch" / "fixtures" / fixture_name

    if not fixture_path.exists():
        pytest.fail(
            f"Missing fixture file: {fixture_path}. Run `python manage.py generate_regression_baseline` first."
        )

    with django_db_blocker.unblock():
        if not Issue.objects.exists():
            # Data migration 0048_populate_curated_tags pre-populates Tags and
            # MeshTagMappings with auto-incremented PKs that conflict with the
            # fixture's hard-coded PKs. Clear them before loading.
            MeshTagMapping.objects.all().delete()
            Tag.objects.all().delete()
            call_command("loaddata", fixture_name, verbosity=0)

        latest_homepage = Homepage.objects.filter(publication_ready=True).order_by("-created").first()
        Homepage.CURRENT_HOMEPAGE = latest_homepage

    yield

    # Flush the test DB so `--reuse-db` doesn't carry baseline rows (subscribers,
    # issues, etc.) into the next session, where they would break tests that
    # assume empty tables.
    with django_db_blocker.unblock():
        call_command("flush", "--no-input", verbosity=0)
        Homepage.CURRENT_HOMEPAGE = None


@pytest.fixture(autouse=True)
def patch_async_tasks(monkeypatch):
    monkeypatch.setattr(
        "spanza_journal_watch.newsletter.tasks.send_confirmation_email.delay",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "spanza_journal_watch.newsletter.tasks.reset_unsubscribe_token.apply_async",
        lambda *args, **kwargs: None,
    )


@pytest.fixture
def route_client(client, regression_baseline):
    return client
