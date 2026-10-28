from django.db import migrations

TASK_NAME = "Purge unconfirmed accounts"
TASK_PATH = "spanza_journal_watch.users.tasks.purge_unconfirmed_accounts"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Daily at 04:15 UTC, after the 03:45 personal-records purge
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="15",
        hour="4",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    PeriodicTask.objects.get_or_create(
        name=TASK_NAME,
        defaults={
            "task": TASK_PATH,
            "crontab": schedule,
            "enabled": True,
            "args": "[]",
            "kwargs": "{}",
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("users", "0006_user_cpd_tracking_enabled_user_is_spanza_member"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
