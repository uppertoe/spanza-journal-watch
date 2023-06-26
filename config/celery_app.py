import os

from celery import Celery

# import sys
# from pathlib import Path


# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

# correctly import the root app folder
# ROOT_DIR = Path(__file__).resolve(strict=True).parent.parent
# sys.path.append(str(ROOT_DIR / "spanza_journal_watch"))

app = Celery("spanza_journal_watch")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()
