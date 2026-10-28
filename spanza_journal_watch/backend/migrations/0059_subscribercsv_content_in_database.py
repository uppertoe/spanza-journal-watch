import logging
from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.db import migrations, models

logger = logging.getLogger(__name__)


def move_uploads_out_of_media(apps, schema_editor):
    """Delete uploaded subscriber lists from media storage now that they are held in the database.

    A list still awaiting import is copied into the database first so the import can finish. If
    that copy cannot be read, the file is left in storage and logged so it can be imported again.
    """
    SubscriberCSV = apps.get_model("backend", "SubscriberCSV")
    for subscriber_csv in SubscriberCSV.objects.exclude(file=""):
        name = subscriber_csv.file.name
        subscriber_csv.filename = PurePosixPath(name).name
        copied = subscriber_csv.processed
        if not copied:
            try:
                with default_storage.open(name, "rb") as upload:
                    subscriber_csv.content = upload.read()
                copied = True
            except Exception:  # noqa: BLE001 — an unreadable file must not block the migration
                logger.warning(
                    "Subscriber list %s could not be read and was left in storage at %s", subscriber_csv.pk, name
                )
        subscriber_csv.save(update_fields=["filename", "content"])
        if not copied:
            continue
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001
            logger.warning("Subscriber list %s could not be deleted from storage at %s", subscriber_csv.pk, name)


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
