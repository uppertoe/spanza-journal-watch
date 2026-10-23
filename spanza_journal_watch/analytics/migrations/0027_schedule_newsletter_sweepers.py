from django.db import migrations

# name -> (task path, minute, hour)
TASKS = {
    "Mark newsletter scanner bursts": (
        "spanza_journal_watch.analytics.tasks.mark_newsletter_scanner_bursts_task",
        "45",
        "*",
    ),
    "Reclassify newsletter opens": (
        "spanza_journal_watch.analytics.tasks.reclassify_newsletter_opens_task",
        "25",
        "4",
    ),
}


def create_schedules(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Bursts hourly at :45, clear of the singleton (:00) and no-JS burst (:30)
    # sweeps; opens daily at 04:25 UTC, after the 04:05 and 04:15 sweeps.
    for name, (path, minute, hour) in TASKS.items():
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        PeriodicTask.objects.get_or_create(
            name=name,
            defaults={"task": path, "crontab": schedule, "enabled": True, "args": "[]", "kwargs": "{}"},
        )


def remove_schedules(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=list(TASKS)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("analytics", "0026_newsletter_automated_reason"),
    ]

    operations = [
        migrations.RunPython(create_schedules, remove_schedules),
    ]
