from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spanza_journal_watch.events"

    def ready(self):
        from . import signals  # noqa: F401
