from django.apps import AppConfig


class LayoutConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "spanza_journal_watch.layout"

    # Ensure the latest homepage is published on startup
    def ready(self):
        from .models import Homepage

        try:
            latest_homepage = Homepage.objects.filter(publication_ready=True).latest("created")
            Homepage.publish_homepage(latest_homepage)
        except:  # noqa
            print("Skipped Homepage import")
