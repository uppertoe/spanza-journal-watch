# Generated by Django 4.1.9 on 2023-08-31 13:55

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0008_alter_elementimage_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="newsletter",
            name="email_token",
            field=models.CharField(default="", editable=False, max_length=64, unique=True),
        ),
    ]
