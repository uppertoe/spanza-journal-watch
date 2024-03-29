# Generated by Django 4.1.9 on 2023-09-01 03:05

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0009_newsletter_email_token"),
        ("analytics", "0003_remove_newsletterclick_email_address_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="newsletterclick",
            name="subscriber",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="newsletter.subscriber"),
        ),
        migrations.AlterField(
            model_name="newsletteropen",
            name="subscriber",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="newsletter.subscriber"),
        ),
    ]
