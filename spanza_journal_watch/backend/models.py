import base64
import hashlib
import uuid

from cryptography.fernet import Fernet, InvalidToken
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


class PlankaIntegrationCredential(TimeStampedModel):
    class AuthMode(models.TextChoices):
        API_KEY = "api_key", "Manual API key"
        PASSWORD = "password", "Username/password"
        OIDC = "oidc", "OIDC code exchange"

    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    auth_mode = models.CharField(max_length=24, choices=AuthMode.choices)
    api_key = models.TextField()
    api_key_prefix = models.CharField(max_length=32, blank=True)
    configured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="configured_planka_credentials",
    )
    last_validated_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Planka Integration Credential"
        verbose_name_plural = "Planka Integration Credentials"

    @staticmethod
    def _derive_fernet_key(secret):
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def _get_fernet(cls):
        secret = (getattr(settings, "PLANKA_CREDENTIAL_ENCRYPTION_KEY", "") or "").strip() or getattr(
            settings, "SECRET_KEY", ""
        )
        return Fernet(cls._derive_fernet_key(secret))

    @classmethod
    def _decrypt_if_possible(cls, value):
        if not value:
            return ""

        try:
            return cls._get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            return None

    @classmethod
    def _encrypt(cls, value):
        return cls._get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    @classmethod
    def get_solo(cls):
        return cls.objects.order_by("pk").first()

    def set_api_key(self, plain_api_key):
        plain_api_key = (plain_api_key or "").strip()
        self.api_key = self._encrypt(plain_api_key) if plain_api_key else ""

    def get_api_key(self):
        stored = self.api_key or ""
        if not stored:
            return ""

        decrypted = self._decrypt_if_possible(stored)
        if decrypted is not None:
            return decrypted

        return stored

    def save(self, *args, **kwargs):
        if self.api_key:
            decrypted = self._decrypt_if_possible(self.api_key)
            if decrypted is None:
                self.api_key = self._encrypt(self.api_key)

        super().save(*args, **kwargs)

    def get_masked_api_key(self):
        api_key = self.get_api_key()
        if not api_key:
            return ""
        if len(api_key) <= 10:
            return "*" * len(api_key)
        return f"{api_key[:6]}…{api_key[-4:]}"

    def __str__(self):
        return f"Planka credential ({self.get_auth_mode_display()})"


class PlankaIssueBinding(TimeStampedModel):
    """
    One Planka project + Reviews board per Issue.
    """

    issue = models.OneToOneField("submissions.Issue", on_delete=models.CASCADE, related_name="planka_binding")
    project_id = models.CharField(max_length=64, unique=True)
    project_name = models.CharField(max_length=255)
    board_id = models.CharField(max_length=64, unique=True)
    board_name = models.CharField(max_length=128, default="Reviews")
    instructions_board_id = models.CharField(max_length=64, blank=True, null=True)
    instructions_board_name = models.CharField(max_length=128, default="Instructions")

    lists = models.JSONField(default=dict, blank=True)
    instructions_lists = models.JSONField(default=dict, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    custom_field_group_id = models.CharField(max_length=64, blank=True, null=True)
    background_asset = models.ForeignKey(
        "backend.PlankaBoardBackgroundAsset",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="issue_bindings",
    )

    class Meta:
        verbose_name = "Planka Issue Binding"
        verbose_name_plural = "Planka Issue Bindings"

    def get_list_id(self, name):
        return (self.lists or {}).get(name)

    def get_custom_field_id(self, name):
        return (self.custom_fields or {}).get(name)

    def __str__(self):
        return f"{self.issue} -> {self.project_name}"


class PlankaBoardBackgroundAsset(TimeStampedModel):
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="backend/planka/backgrounds")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="uploaded_planka_background_assets",
    )

    class Meta:
        ordering = ("name", "-created")
        verbose_name = "Planka Board Background Asset"
        verbose_name_plural = "Planka Board Background Assets"

    def __str__(self):
        return self.name


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
    last_review_modified_at = models.DateTimeField(blank=True, null=True)
    last_card_payload_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ("-created",)
        verbose_name = "Planka Card Import"
        verbose_name_plural = "Planka Card Imports"

    def __str__(self):
        return f"{self.card_name} ({self.card_id})"
