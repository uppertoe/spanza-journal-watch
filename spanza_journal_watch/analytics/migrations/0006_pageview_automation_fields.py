from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0005_newsletter_event_automated_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="pageview",
            name="automated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="pageview",
            name="session_key",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pageview",
            name="user_agent",
            field=models.TextField(blank=True, default=""),
        ),
    ]
