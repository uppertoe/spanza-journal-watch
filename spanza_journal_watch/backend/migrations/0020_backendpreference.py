from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0019_backfill_watched_journal_journal_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackendPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("modified", models.DateTimeField(auto_now=True)),
                ("singleton", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                (
                    "default_watched_journals",
                    models.ManyToManyField(blank=True, related_name="backend_preferences", to="backend.watchedjournal"),
                ),
            ],
            options={
                "verbose_name": "Backend Preference",
                "verbose_name_plural": "Backend Preferences",
            },
        ),
    ]
