# Generated by Django 4.1.9 on 2023-07-16 06:20

from django.db import migrations, models
import spanza_journal_watch.utils.modelmethods


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0025_alter_author_health_services"),
    ]

    operations = [
        migrations.AddField(
            model_name="healthservice",
            name="logo",
            field=models.ImageField(
                blank=True, null=True, upload_to=spanza_journal_watch.utils.modelmethods.name_image
            ),
        ),
        migrations.AddField(
            model_name="healthservice",
            name="logo_authorised",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="author",
            name="health_services",
            field=models.ManyToManyField(blank=True, to="submissions.healthservice"),
        ),
    ]
