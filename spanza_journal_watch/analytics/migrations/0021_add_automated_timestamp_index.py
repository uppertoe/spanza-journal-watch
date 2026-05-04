from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0020_expand_event_type_choices"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["automated", "timestamp"], name="analytics_a_auto_ts_idx"),
        ),
    ]
