# Generated by Django 4.1.9 on 2023-09-13 15:08

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0003_alter_subscribercsv_email_added_count_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscribercsv",
            name="header",
            field=models.BooleanField(default=False),
        ),
    ]
