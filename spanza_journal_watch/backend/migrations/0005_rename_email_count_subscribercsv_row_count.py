# Generated by Django 4.1.9 on 2023-09-14 07:53

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0004_subscribercsv_header"),
    ]

    operations = [
        migrations.RenameField(
            model_name="subscribercsv",
            old_name="email_count",
            new_name="row_count",
        ),
    ]