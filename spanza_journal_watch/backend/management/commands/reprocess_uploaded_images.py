from django.core.management.base import BaseCommand

from spanza_journal_watch.layout.models import FeatureArticle
from spanza_journal_watch.submissions.models import Author, HealthService, Review
from spanza_journal_watch.utils.celerytasks import celery_resize_image, resize_uploaded_image


class Command(BaseCommand):
    help = "Reprocess uploaded frontend-facing images through the Pillow/WebP pipeline."

    TARGETS = (
        ("layout.FeatureArticle", FeatureArticle, "image", 800, "webp"),
        ("submissions.HealthService", HealthService, "logo", 400, "webp"),
        ("submissions.Author", Author, "profile_image", 400, "webp"),
        ("submissions.Review", Review, "feature_image", 800, "original"),
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run inline in the management command instead of queueing Celery tasks.",
        )

    def handle(self, *args, **options):
        total = 0
        sync = options["sync"]

        for model_label, model, field_name, size, target_format in self.TARGETS:
            queryset = model.objects.filter(**{f"{field_name}__isnull": False}).exclude(**{field_name: ""})
            count = queryset.count()
            total += count
            self.stdout.write(f"{model_label}.{field_name} [{target_format}]: {count}")

            for obj in queryset.iterator():
                if sync:
                    resize_uploaded_image(model_label, obj.pk, field_name, size=size, target_format=target_format)
                else:
                    celery_resize_image.delay(
                        model_label,
                        obj.pk,
                        field_name,
                        size=size,
                        target_format=target_format,
                    )

        mode = "processed inline" if sync else "queued"
        self.stdout.write(self.style.SUCCESS(f"{total} image task(s) {mode}."))
