from django.db import migrations


def backfill_source(apps, schema_editor):
    Subscriber = apps.get_model("newsletter", "Subscriber")
    Subscriber.objects.filter(from_csv__isnull=False, source="unknown").update(source="csv_import")
    Subscriber.objects.filter(user__isnull=False, source="unknown").update(source="user_signup")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("newsletter", "0018_subscriber_source"),
    ]

    operations = [
        migrations.RunPython(backfill_source, noop),
    ]
