# Generated by Django 4.1.9 on 2023-08-25 15:08

from django.db import migrations, models
import django.db.models.deletion
import spanza_journal_watch.newsletter.models
import spanza_journal_watch.utils.modelmethods


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0002_alter_newsletter_send_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailFont",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, null=True)),
                (
                    "type",
                    models.CharField(
                        choices=[("TI", "Title"), ("BO", "BODY"), ("OT", "Other")], default="OT", max_length=2
                    ),
                ),
                (
                    "font",
                    models.FileField(
                        blank=True, null=True, upload_to=spanza_journal_watch.utils.modelmethods.name_font
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("modified", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="EmailImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, null=True)),
                (
                    "type",
                    models.CharField(
                        choices=[("HE", "Header"), ("LO", "Logo"), ("OT", "Other")], default="OT", max_length=2
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True, null=True, upload_to=spanza_journal_watch.utils.modelmethods.name_image
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("modified", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name="subscriber",
            name="bounced",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subscriber",
            name="complained",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="subscriber",
            name="tester",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="newsletter",
            name="header_image",
            field=models.ForeignKey(
                blank=True,
                default=spanza_journal_watch.newsletter.models.EmailImage.get_latest_header,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="newsletter.emailimage",
            ),
        ),
        migrations.AddField(
            model_name="newsletter",
            name="title_font",
            field=models.ForeignKey(
                blank=True,
                default=spanza_journal_watch.newsletter.models.EmailFont.get_latest_title,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="newsletter.emailfont",
            ),
        ),
    ]
