# Generated by Django 4.1.9 on 2023-08-30 23:53

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0006_elementimage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="newsletter",
            name="header_image_processed",
            field=models.BooleanField(default=False),
        ),
    ]