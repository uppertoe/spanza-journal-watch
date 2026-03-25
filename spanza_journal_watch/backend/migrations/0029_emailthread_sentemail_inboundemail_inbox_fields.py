import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0028_plankacardrevision_webhook_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_address", models.EmailField()),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("last_message_at", models.DateTimeField()),
                ("has_unread", models.BooleanField(default=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-last_message_at"],
            },
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="thread",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inbound_messages",
                to="backend.emailthread",
            ),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="message_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="in_reply_to",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="inboundemail",
            name="read",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="SentEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recipient", models.EmailField()),
                ("subject", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("message_id", models.CharField(blank=True, max_length=255)),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sent_messages",
                        to="backend.emailthread",
                    ),
                ),
                (
                    "sent_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_emails",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created"],
            },
        ),
    ]
