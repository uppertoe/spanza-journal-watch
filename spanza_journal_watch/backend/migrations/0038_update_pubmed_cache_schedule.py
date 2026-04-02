from django.db import migrations

TASK_NAME = "Refresh PubMed journal cache"
TASK_PATH = "spanza_journal_watch.backend.tasks.refresh_pubmed_journal_cache_task"


def switch_to_crontab(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3,15",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )
    task = PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).first()
    if task:
        task.interval = None
        task.crontab = schedule
        task.save(update_fields=["interval", "crontab"])
    else:
        PeriodicTask.objects.create(
            name=TASK_NAME,
            task=TASK_PATH,
            crontab=schedule,
            enabled=True,
            args="[]",
            kwargs="{}",
        )


def revert_to_interval(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=6,
        period="hours",
    )
    task = PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).first()
    if task:
        task.crontab = None
        task.interval = schedule
        task.save(update_fields=["interval", "crontab"])


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("backend", "0037_migrate_article_data_to_pubmedarticle"),
    ]

    operations = [
        migrations.RunPython(switch_to_crontab, revert_to_interval),
    ]
