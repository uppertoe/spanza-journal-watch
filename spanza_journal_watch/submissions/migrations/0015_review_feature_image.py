# Generated by Django 4.1.9 on 2023-06-27 07:46

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0014_alter_review_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="feature_image",
            field=models.ImageField(
                blank=True,
                height_field="feature_image_height",
                null=True,
                upload_to="uploads/review/",
                width_field="feature_image_width",
            ),
        ),
    ]