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
    file = models.FileField(upload_to="cpd_reports/%Y/%m/", blank=True)
    article_count = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)

    def __str__(self):
        return f"CPD Report {self.pk} ({self.user}) {self.date_from}–{self.date_to}"
