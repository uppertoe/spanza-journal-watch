from django.db import migrations

TASK_NAME = "Prune automated analytics events"
TASK_PATH = "spanza_journal_watch.analytics.tasks.prune_automated_events_task"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Daily at 03:15 UTC — a low-traffic window, and offset from the hourly
    # downgrade sweepers (minute 0 / 30) so the prune never deletes rows out
    # from under a sweep that's still aggregating them.
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="15",
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
        ("analytics", "0021_add_automated_timestamp_index"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
