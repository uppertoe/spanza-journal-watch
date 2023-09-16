# Generated by Django 4.2.5 on 2023-09-16 09:16

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0008_inboundemail"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="subscribercsv",
            options={
                "permissions": [
                    ("manage_subscriber_csv", "Can create and edit CSV subscriber lists"),
                    ("send_newsletters", "Can send out newsletters to all subscribers"),
                    ("view_newesletter_stats", "Can view newsletter open and click statistics"),
                ],
                "verbose_name": "Subscriber list CSV",
            },
        ),
    ]
