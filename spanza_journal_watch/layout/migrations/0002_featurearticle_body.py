# Generated by Django 4.1.9 on 2023-06-07 12:07

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("layout", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="featurearticle",
            name="body",
            field=models.TextField(blank=True, null=True),
        ),
    ]
