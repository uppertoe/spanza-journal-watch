from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("newsletter", "0016_subscriber_email_upper_index"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="newsletter",
            name="send_token",
        ),
    ]
