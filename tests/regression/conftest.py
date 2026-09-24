import datetime
import json
import re
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection

from spanza_journal_watch.layout.models import Homepage
from spanza_journal_watch.submissions.models import Issue, MeshTagMapping, Tag

# The date the committed fixture was generated. The analytics panels count rows
# inside a window ending *today*, so a static fixture walks off its own data as
# the calendar advances: the email snapshot was generated with three subscribers
# inside 180 days and silently became two when the 2026-03-22 row aged out
# around 2026-09-18, turning the suite red on unchanged code. Shifting the whole
# fixture forward by (today - this date) restores the positions the snapshots
# were recorded at, whatever today happens to be.
#
# UPDATE THIS when regenerating regression_baseline.json.
BASELINE_GENERATED_ON = datetime.date(2026, 9, 8)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def _baseline_shift(today=None):
    """Whole weeks between the fixture's generation date and today.

    Whole weeks, not days: charts bucket by week and label weekdays, so an
    arbitrary offset would move Monday and change the rendered HTML. Rounding to
    the nearest week keeps the data within a few days of where it sat at
    generation, which is far inside every window that matters.
    """
    delta = ((today or datetime.date.today()) - BASELINE_GENERATED_ON).days
    return datetime.timedelta(days=round(delta / 7) * 7)


def _shift_iso(value, shift, tz):
    """Move an ISO date or UTC datetime by `shift`, preserving the local clock.

    The fixture stores UTC instants and the app renders them in TIME_ZONE, so
    shifting the UTC wall clock is not enough: moving a row across a daylight
    saving boundary would render it an hour out and change the snapshot. Shift
    the *local* wall clock instead and let the offset be recomputed for the new
    date, which keeps both the displayed time and (because the shift is whole
    weeks) the weekday exactly as recorded.
    """
    if _ISO_DATE.match(value):
        return (datetime.date.fromisoformat(value) + shift).isoformat()
    if not _ISO_DATETIME.match(value):
        return value
    fractional = "." in value
    moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)
    moved = (moment.replace(tzinfo=None) + shift).replace(tzinfo=tz)
    utc = moved.astimezone(datetime.UTC)
    stamp = utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] if fractional else utc.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}Z"


def _shifted_fixture(fixture_path, shift):
    """A copy of the fixture with every ISO date/datetime moved by `shift`."""
    tz = ZoneInfo(settings.TIME_ZONE)
    objects = json.loads(fixture_path.read_text(encoding="utf-8"))
    for obj in objects:
        fields = obj.get("fields") or {}
        for key, value in fields.items():
            if isinstance(value, str):
                fields[key] = _shift_iso(value, shift, tz)
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="regression_baseline_shifted_", delete=False, encoding="utf-8"
    )
    with handle as fh:
        json.dump(objects, fh)
    return Path(handle.name)


# Package scope, not session: the fixture data is flushed as soon as the regression
# package finishes, so tests collected after it (the order differs between
# machines) never see baseline rows such as the curated tags.
@pytest.fixture(scope="package")
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
            shift = _baseline_shift()
            if shift:
                shifted = _shifted_fixture(fixture_path, shift)
                try:
                    call_command("loaddata", str(shifted), verbosity=0)
                finally:
                    shifted.unlink(missing_ok=True)
            else:
                call_command("loaddata", fixture_name, verbosity=0)

        latest_homepage = Homepage.objects.filter(publication_ready=True).order_by("-created").first()
        Homepage.CURRENT_HOMEPAGE = latest_homepage

    yield

    # Flush the test DB so `--reuse-db` doesn't carry baseline rows (subscribers,
    # issues, etc.) into the next session, where they would break tests that
    # assume empty tables.
    with django_db_blocker.unblock():
        # Never flush anything but the test database: if the connection is not
        # pointing at pytest-django's test_* database here, something has gone
        # wrong upstream and wiping the development database would be far worse.
        db_name = connection.settings_dict.get("NAME") or ""
        if not db_name.startswith("test_"):
            raise RuntimeError(f"Refusing to flush non-test database {db_name!r}")
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
