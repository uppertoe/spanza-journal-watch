from django.db import migrations

TASKS = {
    "Downgrade stale-browser bot visitors": (
        "spanza_journal_watch.analytics.tasks.downgrade_stale_browser_visitors_task",
        "5",
    ),
    "Downgrade JS-verified singleton visitors": (
        "spanza_journal_watch.analytics.tasks.downgrade_js_singleton_visitors_task",
        "15",
    ),
}


def create_schedules(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Daily at 04:05 and 04:15 UTC, after the prune (03:15) and UA-cohort
    # (03:45) tasks have finished with the same table.
    for name, (path, minute) in TASKS.items():
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour="4",
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
        ("analytics", "0024_schedule_ua_cohort_downgrade"),
    ]

    operations = [
        migrations.RunPython(create_schedules, remove_schedules),
    ]
