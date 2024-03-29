# Generated by Django 4.1.9 on 2023-08-03 12:35

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0029_article_gin_trgm_idx_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="hit",
            options={"ordering": ("count",)},
        ),
        migrations.RenameIndex(
            model_name="review",
            new_name="submissions_search__36c0ab_gin",
            old_name="submissions_body_708dad_gin",
        ),
        migrations.AddField(
            model_name="review",
            name="publish_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
