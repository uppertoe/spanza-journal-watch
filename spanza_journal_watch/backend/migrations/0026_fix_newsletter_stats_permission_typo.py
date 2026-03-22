from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0025_issuecontributor_role_reviewer_status_pending"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="subscribercsv",
            options={
                "permissions": [
                    ("manage_subscriber_csv", "Can create and edit CSV subscriber lists"),
                    ("send_newsletters", "Can send out newsletters to all subscribers"),
                    ("view_newsletter_stats", "Can view newsletter open and click statistics"),
                ],
                "verbose_name": "Subscriber list CSV",
            },
        ),
    ]
