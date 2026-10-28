from django.conf import settings
from django.db import models
from model_utils.models import TimeStampedModel


class CPDReport(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        GENERATING = "generating", "Generating"
        READY = "ready", "Ready"
        ERROR = "error", "Error"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cpd_reports")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    date_from = models.DateField()
    date_to = models.DateField()
    # Held in the database with the reading record it is built from, and
    # served only to its owner. Reports are deleted after 30 days.
    pdf = models.BinaryField(blank=True, editable=False)
    article_count = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)

    def __str__(self):
        return f"CPD Report {self.pk} ({self.user}) {self.date_from}–{self.date_to}"
