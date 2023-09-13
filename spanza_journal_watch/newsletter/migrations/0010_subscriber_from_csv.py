# Generated by Django 4.1.9 on 2023-09-12 23:57

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0001_initial"),
        ("newsletter", "0009_newsletter_email_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriber",
            name="from_csv",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="backend.subscribercsv",
                verbose_name="Uploaded via CSV",
            ),
        ),
    ]
