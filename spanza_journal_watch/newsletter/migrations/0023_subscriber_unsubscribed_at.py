from django.db import migrations, models
from django.db.models import F


def backfill_unsubscribed_at(apps, schema_editor):
    # The last-modified time is the best record of when a current unsubscribe happened.
    Subscriber = apps.get_model("newsletter", "Subscriber")
    Subscriber.objects.filter(subscribed=False, unsubscribed_at__isnull=True).update(unsubscribed_at=F("modified"))


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0022_newsletter_scheduled_send"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriber",
            name="unsubscribed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_unsubscribed_at, migrations.RunPython.noop),
    ]
