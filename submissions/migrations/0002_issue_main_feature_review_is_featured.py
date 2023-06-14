# Generated by Django 4.1.9 on 2023-06-07 11:52

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("layout", "0001_initial"),
        ("submissions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="main_feature",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="layout.featurearticle"
            ),
        ),
        migrations.AddField(
            model_name="review",
            name="is_featured",
            field=models.BooleanField(default=False),
        ),
    ]