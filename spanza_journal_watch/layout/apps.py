from django.apps import AppConfig


class LayoutConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spanza_journal_watch.layout"

    def ready(self):
        from . import signals  # noqa: F401
