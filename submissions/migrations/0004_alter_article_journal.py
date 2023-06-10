# Generated by Django 4.1.9 on 2023-06-10 13:29

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0003_alter_issue_main_feature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="article",
            name="journal",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="submissions.journal"
            ),
        ),
    ]
