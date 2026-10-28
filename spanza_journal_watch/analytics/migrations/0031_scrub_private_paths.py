from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.db import migrations
from django.db.models import Q, TextField
from django.db.models.functions import Cast

# A copy of analytics.utils.PRIVATE_PATH_PREFIXES as it stood when this ran.
PRIVATE_PATH_PREFIXES = ("/newsletter/", "/users/", "/accounts/", "/invites/")
TRACKING_TOKEN_PARAM = "t"
BATCH_SIZE = 2000


def _scrub_url(value):
    if not isinstance(value, str) or not (value.startswith("/") or "://" in value):
        return value
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    for prefix in PRIVATE_PATH_PREFIXES:
        if parsed.path.startswith(prefix):
            return prefix
    if parsed.query:
        query = parse_qsl(parsed.query, keep_blank_values=True)
        kept = [(key, item) for key, item in query if key != TRACKING_TOKEN_PARAM]
        if len(kept) != len(query):
            return urlunparse(parsed._replace(query=urlencode(kept)))
    return value


def _scrub(value):
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return _scrub_url(value)


def scrub_private_paths(apps, schema_editor):
    AnalyticsEvent = apps.get_model("analytics", "AnalyticsEvent")

    for prefix in PRIVATE_PATH_PREFIXES:
        AnalyticsEvent.objects.filter(landing_page__startswith=prefix).exclude(landing_page=prefix).update(
            landing_page=prefix
        )

    # Narrow to rows whose metadata mentions a private path or a tracking token,
    # then rewrite those in primary-key batches.
    mentions = Q()
    for needle in (*PRIVATE_PATH_PREFIXES, f"?{TRACKING_TOKEN_PARAM}=", f"&{TRACKING_TOKEN_PARAM}="):
        mentions |= Q(metadata_text__contains=needle)
    candidates = (
        AnalyticsEvent.objects.annotate(metadata_text=Cast("metadata", TextField())).filter(mentions).order_by("pk")
    )

    last_pk = 0
    while True:
        batch = list(candidates.filter(pk__gt=last_pk).only("pk", "metadata")[:BATCH_SIZE])
        if not batch:
            break
        last_pk = batch[-1].pk
        changed = []
        for event in batch:
            scrubbed = _scrub(event.metadata)
            if scrubbed != event.metadata:
                event.metadata = scrubbed
                changed.append(event)
        if changed:
            AnalyticsEvent.objects.bulk_update(changed, ["metadata"])


class Migration(migrations.Migration):
    # Each batch commits on its own, so a large table is not held in one transaction.
    atomic = False

    dependencies = [
        ("analytics", "0030_schedule_personal_records_purge"),
    ]

    operations = [
        migrations.RunPython(scrub_private_paths, migrations.RunPython.noop, elidable=True),
    ]
