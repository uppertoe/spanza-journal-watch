from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0020_backendpreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="pubmedimportbatch",
            name="task_action",
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AddField(
            model_name="pubmedimportbatch",
            name="task_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="pubmedimportbatch",
            name="task_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="pubmedimportbatch",
            name="task_state",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("error", "Error"),
                ],
                default="idle",
                max_length=16,
            ),
        ),
    ]
