from django.db import migrations

# Events that record a newsletter or email click, or a subscription, keep their
# subscriber. Everything else is reading activity, now held against the
# anonymous visitor only.
KEPT_SOURCES = ("newsletter_click", "email_click")
KEPT_EVENT_TYPES = ("newsletter_subscribe",)


def unlink_reading(apps, schema_editor):
    AnalyticsEvent = apps.get_model("analytics", "AnalyticsEvent")
    AnalyticsEvent.objects.filter(subscriber__isnull=False).exclude(source__in=KEPT_SOURCES).exclude(
        event_type__in=KEPT_EVENT_TYPES
    ).update(subscriber=None)


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0028_hash_session_keys"),
    ]

    operations = [
        migrations.RunPython(unlink_reading, migrations.RunPython.noop, elidable=True),
    ]
