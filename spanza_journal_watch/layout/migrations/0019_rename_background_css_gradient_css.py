# Generated by Django 4.1.9 on 2023-07-09 08:30

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("layout", "0018_rename_gradient_name_gradient_name"),
    ]

    operations = [
        migrations.RenameField(
            model_name="gradient",
            old_name="background_css",
            new_name="css",
        ),
    ]
