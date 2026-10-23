from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0025_schedule_stale_browser_and_js_singleton"),
    ]

    operations = [
        migrations.AddField(
            model_name="newsletterclick",
            name="automated_reason",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="newsletteropen",
            name="automated_reason",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
