# Generated by Django 4.1.9 on 2023-07-14 01:16

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0024_author_slug"),
    ]

    operations = [
        migrations.AlterField(
            model_name="author",
            name="health_services",
            field=models.ManyToManyField(blank=True, null=True, to="submissions.healthservice"),
        ),
    ]
