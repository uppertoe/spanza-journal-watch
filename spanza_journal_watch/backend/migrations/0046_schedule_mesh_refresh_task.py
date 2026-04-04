from django.db import migrations

TASK_NAME = "Refresh missing MeSH terms"
TASK_PATH = "spanza_journal_watch.backend.tasks.refresh_mesh_terms_task"


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Run weekly on Sunday at 04:00 UTC
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="4",
        day_of_week="0",
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
        ("backend", "0045_watchedjournal_add_medline_ta_nlm_id_display_name"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
