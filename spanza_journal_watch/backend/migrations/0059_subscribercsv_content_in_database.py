from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.db import migrations, models


def move_uploads_out_of_media(apps, schema_editor):
    """Delete uploaded subscriber lists from media storage now that they are held in the database.

    A list still awaiting import is copied into the database first so the import can finish.
    """
    SubscriberCSV = apps.get_model("backend", "SubscriberCSV")
    for subscriber_csv in SubscriberCSV.objects.exclude(file=""):
        name = subscriber_csv.file.name
        subscriber_csv.filename = PurePosixPath(name).name
        if not subscriber_csv.processed:
            try:
                with default_storage.open(name, "rb") as upload:
                    subscriber_csv.content = upload.read()
            except Exception:  # noqa: BLE001 — a missing file must not block the migration
                pass
        subscriber_csv.save(update_fields=["filename", "content"])
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001
            pass


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0058_schedule_analytics_visits_warming"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscribercsv",
            name="filename",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="subscribercsv",
            name="content",
            field=models.BinaryField(blank=True, editable=False),
        ),
        migrations.RunPython(move_uploads_out_of_media, migrations.RunPython.noop),
        migrations.RemoveField(model_name="subscribercsv", name="file"),
    ]
