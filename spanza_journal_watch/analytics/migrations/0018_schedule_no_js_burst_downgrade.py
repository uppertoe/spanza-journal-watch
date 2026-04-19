from django.db import migrations

TASK_NAME = "Downgrade no-JS burst visitors"
TASK_PATH = "spanza_journal_watch.analytics.tasks.downgrade_no_js_burst_visitors_task"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Hourly at minute 30 — staggered from the singleton sweeper at minute 0
    # so they don't both hit the analytics table at the same instant.
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="30",
        hour="*",
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
        ("analytics", "0017_singleton_downgrade_hourly"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
