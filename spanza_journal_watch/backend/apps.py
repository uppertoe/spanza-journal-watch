from django.apps import AppConfig


class BackendConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spanza_journal_watch.backend"

    def ready(self):
        from spanza_journal_watch.utils.lookups import register_lookups

        register_lookups()
        # Implicitly connect signal handlers decorated with @receiver.
        from . import signals  # noqa
