import base64
import hashlib
import secrets
import uuid
from email.utils import formataddr

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models
from django.utils import timezone
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
            ("view_newsletter_stats", "Can view newsletter open and click statistics"),
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


class EmailThread(models.Model):
    """Groups inbound and outbound emails into a conversation with one external address."""

    external_address = models.EmailField()
    subject = models.CharField(max_length=255, blank=True)
    last_message_at = models.DateTimeField()
    has_unread = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.external_address} — {self.subject or '(no subject)'}"


class InboundEmail(models.Model):
    thread = models.ForeignKey(
        EmailThread,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_messages",
    )
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
    message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.CharField(max_length=255, blank=True)
    read = models.BooleanField(default=False)

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


class SentEmail(models.Model):
    """Outbound reply sent by a staff member via the inbox interface."""

    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name="sent_messages")
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    message_id = models.CharField(max_length=255, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_emails",
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created"]

    def __str__(self):
        return f"Reply to {self.recipient} ({self.created:%Y-%m-%d})"


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
    webhook_id = models.CharField(max_length=64, blank=True)

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


class PlankaCardRevision(models.Model):
    """
    Snapshot of a Planka card description, recorded via webhook on each change.
    Capped at 100 revisions per card (oldest deleted on insert).
    """

    REVISION_CAP = 100

    binding = models.ForeignKey(
        PlankaIssueBinding,
        on_delete=models.CASCADE,
        related_name="card_revisions",
    )
    card_id = models.CharField(max_length=64, db_index=True)
    card_name = models.CharField(max_length=1024, blank=True)
    board_id = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    description_hash = models.CharField(max_length=64, blank=True)
    actor_email = models.EmailField(blank=True)
    actor_name = models.CharField(max_length=255, blank=True)
    source = models.CharField(
        max_length=16,
        choices=[("webhook", "Webhook"), ("snapshot", "Initial snapshot")],
        default="webhook",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created",)
        verbose_name = "Planka Card Revision"
        verbose_name_plural = "Planka Card Revisions"
        indexes = [
            models.Index(fields=["card_id", "-created"]),
        ]

    @classmethod
    def record(
        cls, binding, card_id, card_name, board_id, description, actor_email="", actor_name="", source="webhook"
    ):
        """
        Save a new revision, skipping if description hash is identical to the most recent.
        Enforces the cap by deleting the oldest beyond REVISION_CAP.
        Returns (revision, created) tuple.
        """
        description_hash = hashlib.sha256((description or "").encode("utf-8")).hexdigest()

        # Deduplicate: skip if last revision is identical
        last = cls.objects.filter(card_id=card_id).order_by("-created").first()
        if last and last.description_hash == description_hash:
            return last, False

        revision = cls.objects.create(
            binding=binding,
            card_id=card_id,
            card_name=card_name,
            board_id=board_id,
            description=description or "",
            description_hash=description_hash,
            actor_email=actor_email,
            actor_name=actor_name,
            source=source,
        )

        # Enforce cap: delete oldest beyond limit
        ids_to_keep = list(
            cls.objects.filter(card_id=card_id).order_by("-created").values_list("pk", flat=True)[: cls.REVISION_CAP]
        )
        cls.objects.filter(card_id=card_id).exclude(pk__in=ids_to_keep).delete()

        return revision, True

    def __str__(self):
        return f"{self.card_name} @ {self.created:%Y-%m-%d %H:%M}"


class IssueContributor(TimeStampedModel):
    class Role(models.TextChoices):
        COORDINATOR = "coordinator", "Coordinator"
        REVIEWER = "reviewer", "Reviewer"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    class PlankaSyncState(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "OK"
        ERROR = "error", "Error"

    issue = models.ForeignKey("submissions.Issue", on_delete=models.CASCADE, related_name="contributors")
    email = models.EmailField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="issue_contributor_memberships",
    )
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.REVIEWER)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.INVITED)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="issue_contributor_invites_sent",
    )
    invited_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    name = models.CharField(max_length=255, blank=True)
    author = models.ForeignKey(
        "submissions.Author",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="contributor_memberships",
        help_text="Linked Author profile (auto-matched by email on invite acceptance)",
    )
    planka_user_id = models.CharField(max_length=64, blank=True)
    planka_membership_id = models.CharField(max_length=64, blank=True)
    planka_instructions_membership_id = models.CharField(max_length=64, blank=True)
    planka_sync_state = models.CharField(
        max_length=24, choices=PlankaSyncState.choices, default=PlankaSyncState.PENDING
    )
    planka_last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("email", "pk")
        constraints = [
            models.UniqueConstraint(fields=["issue", "email"], name="uniq_issue_contributor_email"),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.issue}: {self.email} ({self.get_role_display()})"


class IssueContributorInvite(TimeStampedModel):
    contributor = models.ForeignKey(IssueContributor, on_delete=models.CASCADE, related_name="invites")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_issue_contributor_invites",
    )
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created",)

    @staticmethod
    def generate_raw_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()

    def is_active(self):
        return not self.consumed_at and self.expires_at > timezone.now()

    def __str__(self):
        return f"Invite for {self.contributor.email} ({self.contributor.issue})"


class PubmedIntegrationCredential(TimeStampedModel):
    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    api_key = models.TextField(blank=True)
    configured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="configured_pubmed_credentials",
    )
    last_validated_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "PubMed Integration Credential"
        verbose_name_plural = "PubMed Integration Credentials"

    @staticmethod
    def _derive_fernet_key(secret):
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def _get_fernet(cls):
        secret = (getattr(settings, "PUBMED_CREDENTIAL_ENCRYPTION_KEY", "") or "").strip() or getattr(
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
        if len(api_key) <= 8:
            return "*" * len(api_key)
        return f"{api_key[:4]}…{api_key[-4:]}"

    def __str__(self):
        return "PubMed credential"


class WatchedJournal(TimeStampedModel):
    name = models.CharField(max_length=255)
    journal = models.ForeignKey(
        "submissions.Journal",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="watched_sources",
    )
    issn_print = models.CharField(max_length=32, blank=True)
    issn_electronic = models.CharField(max_length=32, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()

        super().save(*args, **kwargs)

        if self.journal_id or not self.name:
            return

        from spanza_journal_watch.submissions.models import Journal

        journal = Journal.objects.filter(name__iexact=self.name).order_by("pk").first()
        if journal is None:
            journal = Journal.objects.create(name=self.name, active=True)

        WatchedJournal.objects.filter(pk=self.pk, journal__isnull=True).update(journal=journal)
        self.journal_id = journal.pk

    def __str__(self):
        return self.name


class BackendPreference(TimeStampedModel):
    DEFAULT_INBOX_FROM_NAME = "Journal Watch Admin"
    DEFAULT_INBOX_FROM_ADDRESS = "admin@journalwatch.org.au"

    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    default_watched_journals = models.ManyToManyField(WatchedJournal, blank=True, related_name="backend_preferences")
    inbox_from_name = models.CharField(max_length=255, blank=True, default="")
    inbox_from_address = models.EmailField(blank=True, default="")

    class Meta:
        verbose_name = "Backend Preference"
        verbose_name_plural = "Backend Preferences"

    @classmethod
    def get_solo(cls):
        return cls.objects.order_by("pk").first()

    def __str__(self):
        return "Backend preferences"

    def get_inbox_from_name(self):
        return (self.inbox_from_name or "").strip() or self.DEFAULT_INBOX_FROM_NAME

    def get_inbox_from_address(self):
        return (self.inbox_from_address or "").strip().lower() or self.DEFAULT_INBOX_FROM_ADDRESS

    def get_inbox_from_email(self):
        return formataddr((self.get_inbox_from_name(), self.get_inbox_from_address()))


class PubmedImportBatch(TimeStampedModel):
    TASK_STATE_IDLE = "idle"
    TASK_STATE_PENDING = "pending"
    TASK_STATE_RUNNING = "running"
    TASK_STATE_SUCCESS = "success"
    TASK_STATE_ERROR = "error"
    TASK_STATE_CHOICES = (
        (TASK_STATE_IDLE, "Idle"),
        (TASK_STATE_PENDING, "Pending"),
        (TASK_STATE_RUNNING, "Running"),
        (TASK_STATE_SUCCESS, "Success"),
        (TASK_STATE_ERROR, "Error"),
    )

    issue = models.ForeignKey(
        "submissions.Issue",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pubmed_import_batches",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pubmed_import_batches",
    )
    from_month = models.DateField()
    to_month = models.DateField()
    keyword_query = models.CharField(max_length=255, blank=True)
    watched_journals = models.ManyToManyField(WatchedJournal, related_name="import_batches", blank=True)
    result_count = models.PositiveIntegerField(default=0)
    selected_count = models.PositiveIntegerField(default=0)
    task_state = models.CharField(max_length=16, choices=TASK_STATE_CHOICES, default=TASK_STATE_IDLE)
    task_action = models.CharField(max_length=24, blank=True)
    task_id = models.CharField(max_length=64, blank=True)
    task_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)

    def __str__(self):
        return f"PubMed import {self.from_month:%Y-%m} → {self.to_month:%Y-%m}"


class PubmedArticle(TimeStampedModel):
    pmid = models.CharField(max_length=32, unique=True)
    doi = models.CharField(max_length=255, blank=True, null=True, unique=True)
    title = models.TextField(blank=True)
    abstract = models.TextField(blank=True)
    source_journal_name = models.CharField(max_length=255, blank=True)
    publication_date = models.DateField(blank=True, null=True)
    publication_month = models.DateField(blank=True, null=True)
    article_url = models.URLField(max_length=500, blank=True)
    pubmed_url = models.URLField(max_length=500, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-publication_date", "-created")

    def save(self, *args, **kwargs):
        doi = (self.doi or "").strip().lower()
        self.doi = doi or None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title or f"PMID {self.pmid}"


class PubmedBatchArticle(TimeStampedModel):
    batch = models.ForeignKey(PubmedImportBatch, on_delete=models.CASCADE, related_name="batch_articles")
    article = models.ForeignKey(PubmedArticle, on_delete=models.CASCADE, related_name="batch_links")
    watched_journal = models.ForeignKey(
        WatchedJournal,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="batch_articles",
    )
    issue = models.ForeignKey(
        "submissions.Issue",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pubmed_batch_articles",
    )
    is_selected = models.BooleanField(default=False)
    planka_card_id = models.CharField(max_length=64, blank=True)
    planka_card_url = models.URLField(max_length=500, blank=True)
    planka_pushed_at = models.DateTimeField(blank=True, null=True)
    planka_push_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created",)
        constraints = [
            models.UniqueConstraint(fields=["batch", "article"], name="uniq_pubmed_batch_article"),
        ]

    def __str__(self):
        return f"{self.batch_id}: {self.article}"
