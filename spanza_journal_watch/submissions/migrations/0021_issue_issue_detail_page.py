# Generated by Django 4.1.9 on 2023-07-08 06:32

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("layout", "0012_issuedetailpage"),
        ("submissions", "0020_alter_review_feature_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="issue_detail_page",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="issue_detail_page",
                to="layout.issuedetailpage",
            ),
        ),
    ]
