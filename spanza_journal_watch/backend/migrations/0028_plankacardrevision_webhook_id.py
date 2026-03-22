import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0027_issuecontributor_author"),
    ]

    operations = [
        migrations.AddField(
            model_name="plankaissuebinding",
            name="webhook_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name="PlankaCardRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("card_id", models.CharField(db_index=True, max_length=64)),
                ("card_name", models.CharField(blank=True, max_length=1024)),
                ("board_id", models.CharField(blank=True, max_length=64)),
                ("description", models.TextField(blank=True)),
                ("description_hash", models.CharField(blank=True, max_length=64)),
                ("actor_email", models.EmailField(blank=True)),
                ("actor_name", models.CharField(blank=True, max_length=255)),
                (
                    "source",
                    models.CharField(
                        choices=[("webhook", "Webhook"), ("snapshot", "Initial snapshot")],
                        default="webhook",
                        max_length=16,
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="card_revisions",
                        to="backend.plankaissuebinding",
                    ),
                ),
            ],
            options={
                "verbose_name": "Planka Card Revision",
                "verbose_name_plural": "Planka Card Revisions",
                "ordering": ("-created",),
            },
        ),
        migrations.AddIndex(
            model_name="plankacardrevision",
            index=models.Index(fields=["card_id", "-created"], name="backend_pla_card_id_idx"),
        ),
    ]
