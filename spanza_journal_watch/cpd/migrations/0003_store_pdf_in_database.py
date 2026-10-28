from django.core.files.storage import default_storage
from django.db import migrations, models


def delete_stored_reports(apps, schema_editor):
    """Remove report PDFs from media storage now that they are held in the database.

    Members can regenerate a report in seconds, so the rows go too rather than
    copying each file into the database.
    """
    CPDReport = apps.get_model("cpd", "CPDReport")
    for name in CPDReport.objects.exclude(file="").values_list("file", flat=True):
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001 — a missing file must not block the migration
            pass
    CPDReport.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cpd", "0002_schedule_cpd_report_cleanup"),
    ]

    operations = [
        migrations.RunPython(delete_stored_reports, migrations.RunPython.noop),
        migrations.RemoveField(model_name="cpdreport", name="file"),
        migrations.AddField(
            model_name="cpdreport",
            name="pdf",
            field=models.BinaryField(blank=True, editable=False),
        ),
    ]
