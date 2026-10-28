from django.db import migrations
from django.utils.crypto import salted_hmac

SALT = "spanza_journal_watch.analytics.session_fingerprint"
BATCH = 2000


def _fingerprint(session_key):
    return salted_hmac(SALT, session_key, algorithm="sha256").hexdigest()


def hash_session_keys(apps, schema_editor, app_label, model_name):
    """Replace stored session keys with their fingerprints, in batches, in SQL."""
    model = apps.get_model(app_label, model_name)
    table = schema_editor.quote_name(model._meta.db_table)
    keys = list(model.objects.exclude(session_key="").values_list("session_key", flat=True).distinct().order_by())
    # A 64-character hex value is already a fingerprint (Django session keys are 32 characters)
    keys = [key for key in keys if len(key) != 64]
    with schema_editor.connection.cursor() as cursor:
        for start in range(0, len(keys), BATCH):
            batch = keys[start : start + BATCH]
            values = ", ".join(["(%s, %s)"] * len(batch))
            params = [item for key in batch for item in (key, _fingerprint(key))]
            cursor.execute(
                f"UPDATE {table} AS t SET session_key = m.fingerprint "
                f"FROM (VALUES {values}) AS m(session_key, fingerprint) "
                f"WHERE t.session_key = m.session_key",
                params,
            )


def hash_event_session_keys(apps, schema_editor):
    hash_session_keys(apps, schema_editor, "analytics", "AnalyticsEvent")


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0027_schedule_newsletter_sweepers"),
    ]

    operations = [
        migrations.RunPython(hash_event_session_keys, migrations.RunPython.noop, elidable=True),
    ]
