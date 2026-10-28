from django.db import migrations

TASK_NAME = "Purge expired personal records"
TASK_PATH = "spanza_journal_watch.analytics.tasks.purge_expired_personal_records"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Daily at 03:45 UTC, after the 03:15 automated-event prune
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="45",
        hour="3",
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
        ("analytics", "0029_unlink_reading_from_subscribers"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
