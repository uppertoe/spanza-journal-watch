import base64
import uuid

from django.conf import settings
from django.db import models
from django.utils.html import escape, strip_tags

from spanza_journal_watch.utils.modelmethods import name_csv
from spanza_journal_watch.utils.models import TimeStampedModel


class SubscriberCSV(models.Model):
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=name_csv)
    confirmed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    modified = models.DateTimeField(auto_now=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    email_added_count = models.PositiveIntegerField(null=True, blank=True)
    save_token = models.CharField(max_length=64, blank=True, null=True)
    header = models.BooleanField(default=False)

    class Meta:
        permissions = [
            ("manage_subscriber_csv", "Can create and edit CSV subscriber lists"),
            ("send_newsletters", "Can send out newsletters to all subscribers"),
            ("view_newesletter_stats", "Can view newsletter open and click statistics"),
        ]
        verbose_name = "Subscriber list CSV"

    def generate_save_token(self):
        r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
        return r_uuid.replace("=", "")

    def is_ready_to_process(self):
        return self.confirmed and not self.processed

    def save(self, *args, **kwargs):
        # Refresh the save token
        self.save_token = self.generate_save_token()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class InboundEmail(models.Model):
    sender = models.EmailField(null=True, blank=True)
    recipient = models.EmailField(null=True, blank=True)
    header_sender = models.CharField(max_length=255, null=True, blank=True)
    header_recipients = models.TextField(null=True, blank=True)
    subject = models.CharField(max_length=255, null=True, blank=True)
    body = models.TextField(null=True, blank=True)
    body_html = models.TextField(null=True, blank=True)
    sent_timestamp = models.DateTimeField(null=True, blank=True)
    attachments = models.BooleanField(default=False)
    email_file = models.CharField(max_length=255, null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    def get_stripped_body_html(self):
        return escape(strip_tags(self.body_html))

    def get_raw_email(self):
        if settings.DEBUG:
            return self.email_file
        else:
            bucket = settings.AWS_STORAGE_BUCKET_NAME
            region = settings.AWS_S3_REGION_NAME
            prefix = settings.INBOUND_S3_OBJECT_PREFIX
            return f"https://{bucket}.s3.{region}.amazonaws.com/{prefix}/{self.email_file}"

    def __str__(self):
        created = self.created.strftime("%m/%d/%Y, %H:%M:%S")
        return f"Email from {self.sender} - received {created}"


class PlankaIssueBinding(TimeStampedModel):
    """
    One Planka project + Reviews board per Issue.
    """

    issue = models.OneToOneField("submissions.Issue", on_delete=models.CASCADE, related_name="planka_binding")
    project_id = models.CharField(max_length=64, unique=True)
    project_name = models.CharField(max_length=255)
    board_id = models.CharField(max_length=64, unique=True)
    board_name = models.CharField(max_length=128, default="Reviews")

    lists = models.JSONField(default=dict, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    custom_field_group_id = models.CharField(max_length=64, blank=True, null=True)

    class Meta:
        verbose_name = "Planka Issue Binding"
        verbose_name_plural = "Planka Issue Bindings"

    def get_list_id(self, name):
        return (self.lists or {}).get(name)

    def get_custom_field_id(self, name):
        return (self.custom_fields or {}).get(name)

    def __str__(self):
        return f"{self.issue} -> {self.project_name}"


class PlankaCardImport(TimeStampedModel):
    """
    Ledger of imported Planka cards to prevent duplicate imports.
    """

    issue = models.ForeignKey("submissions.Issue", on_delete=models.CASCADE, related_name="planka_imports")
    binding = models.ForeignKey(PlankaIssueBinding, on_delete=models.CASCADE, related_name="imports")
    card_id = models.CharField(max_length=64, unique=True)
    card_name = models.CharField(max_length=1024)
    review = models.ForeignKey(
        "submissions.Review",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="planka_imports",
    )
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)

    class Meta:
        ordering = ("-created",)
        verbose_name = "Planka Card Import"
        verbose_name_plural = "Planka Card Imports"

    def __str__(self):
        return f"{self.card_name} ({self.card_id})"
