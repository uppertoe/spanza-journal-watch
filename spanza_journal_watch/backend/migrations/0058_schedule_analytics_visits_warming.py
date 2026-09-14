from django.db import migrations

TASK_NAME = "Warm analytics derived visits"
TASK_PATH = "spanza_journal_watch.backend.tasks.warm_analytics_visits_task"


def create_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Well inside the 15-minute cache life, so the dashboards never rebuild on request.
    schedule, _ = IntervalSchedule.objects.get_or_create(every=8, period="minutes")
    PeriodicTask.objects.get_or_create(
        name=TASK_NAME,
        defaults={
            "task": TASK_PATH,
            "interval": schedule,
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
        ("backend", "0057_pubmedarticle_topics"),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
