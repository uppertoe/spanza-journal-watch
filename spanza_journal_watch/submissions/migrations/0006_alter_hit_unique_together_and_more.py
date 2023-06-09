# Generated by Django 4.1.9 on 2023-06-14 13:52

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("submissions", "0005_alter_issue_date"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="hit",
            unique_together={("content_type", "object_id")},
        ),
        migrations.AddIndex(
            model_name="hit",
            index=models.Index(fields=["content_type", "object_id"], name="submissions_content_d4e2a4_idx"),
        ),
    ]
