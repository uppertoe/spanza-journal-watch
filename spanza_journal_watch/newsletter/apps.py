from django.apps import AppConfig


class NewsletterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spanza_journal_watch.newsletter"

    def ready(self):
        # Implicitly connect signal handlers decorated with @receiver.
        from . import signals  # noqa
