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
            ("view_site_analytics", "Can view site analytics (overview, content, traffic, search, journals)"),
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


class ChiefEditorInvite(TimeStampedModel):
    """Invitation to promote a user to chief editor."""

    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="chief_editor_invite_accepted",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="chief_editor_invites_sent",
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
        return f"Chief editor invite for {self.email}"


class FetchLog(TimeStampedModel):
    """Records each NIH/PubMed fetch operation for monitoring."""

    TASK_CACHE_REFRESH = "cache_refresh"
    TASK_BATCH_IMPORT = "batch_import"
    TASK_BATCH_PUSH = "batch_push"
    TASK_TYPE_CHOICES = [
        (TASK_CACHE_REFRESH, "Cache refresh"),
        (TASK_BATCH_IMPORT, "Batch import"),
        (TASK_BATCH_PUSH, "Batch push"),
    ]

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_ERROR, "Error"),
    ]

    task_type = models.CharField(max_length=24, choices=TASK_TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    celery_task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)
    duration_seconds = models.FloatField(blank=True, null=True)
    journal_count = models.PositiveIntegerField(default=0)
    articles_created = models.PositiveIntegerField(default=0)
    articles_touched = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-started_at",)

    def finish(self, status, **kwargs):
        self.status = status
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    def __str__(self):
        return f"{self.get_task_type_display()} ({self.status}) — {self.started_at:%Y-%m-%d %H:%M}"


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
    class Source(models.TextChoices):
        PUBMED = "pubmed", "PubMed"
        CROSSREF = "crossref", "CrossRef"

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
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PUBMED)
    active = models.BooleanField(default=True)
    visible_on_frontend = models.BooleanField(default=True)

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


class WatchedJournalArticle(TimeStampedModel):
    watched_journal = models.ForeignKey(WatchedJournal, on_delete=models.CASCADE, related_name="journal_articles")
    article = models.ForeignKey("backend.PubmedArticle", on_delete=models.CASCADE, related_name="journal_links")
    publication_month = models.DateField(blank=True, null=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-publication_month", "-last_seen_at")
        indexes = [
            models.Index(fields=["publication_month"], name="backend_wja_pub_month_idx"),
            models.Index(fields=["watched_journal", "publication_month"], name="backend_wja_journal_month_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["watched_journal", "article"], name="uniq_watched_journal_article")
        ]

    def __str__(self):
        return f"{self.watched_journal} → {self.article}"


class PubmedArticleUserState(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pubmed_article_states")
    article = models.ForeignKey("backend.PubmedArticle", on_delete=models.CASCADE, related_name="user_states")
    starred_at = models.DateTimeField(blank=True, null=True)
    recommended_at = models.DateTimeField(blank=True, null=True)
    read_at = models.DateTimeField(blank=True, null=True)
    full_text_clicked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["article", "recommended_at"], name="backend_paus_article_rec_idx"),
            models.Index(fields=["user", "starred_at"], name="backend_paus_user_star_idx"),
            models.Index(fields=["user", "read_at"], name="backend_paus_user_read_idx"),
            models.Index(fields=["user", "full_text_clicked_at"], name="backend_paus_user_ftclick_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "article"], name="uniq_pubmed_article_user_state"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.article_id}"


class BackendPreference(TimeStampedModel):
    DEFAULT_INBOX_FROM_NAME = "Journal Watch Admin"
    DEFAULT_INBOX_FROM_ADDRESS = "admin@journalwatch.org.au"

    class BannerTone(models.TextChoices):
        INFO = "info", "Information"
        SUCCESS = "success", "Success"
        WARNING = "warning", "Warning"
        PRIMARY = "primary", "Primary"

    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    default_watched_journals = models.ManyToManyField(WatchedJournal, blank=True, related_name="backend_preferences")
    inbox_from_name = models.CharField(max_length=255, blank=True, default="")
    inbox_from_address = models.EmailField(blank=True, default="")
    frontend_banner_enabled = models.BooleanField(default=False)
    frontend_banner_title = models.CharField(max_length=120, blank=True, default="")
    frontend_banner_text = models.TextField(blank=True, default="")
    frontend_banner_link_text = models.CharField(max_length=80, blank=True, default="")
    frontend_banner_link_url = models.CharField(max_length=500, blank=True, default="")
    frontend_banner_tone = models.CharField(
        max_length=16,
        choices=BannerTone.choices,
        default=BannerTone.PRIMARY,
    )

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

    def get_frontend_banner(self):
        if not self.frontend_banner_enabled:
            return None

        text = (self.frontend_banner_text or "").strip()
        title = (self.frontend_banner_title or "").strip()
        if not text and not title:
            return None

        return {
            "title": title,
            "text": text,
            "link_text": (self.frontend_banner_link_text or "").strip(),
            "link_url": (self.frontend_banner_link_url or "").strip(),
            "tone": (self.frontend_banner_tone or self.BannerTone.PRIMARY).strip(),
        }


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
    TRUNCATED_NAME_LENGTH = 50

    # PubMed fields
    pmid = models.CharField(max_length=32, unique=True, blank=True, null=True)
    doi = models.CharField(max_length=255, blank=True, null=True, unique=True)
    title = models.TextField(blank=True)
    abstract = models.TextField(blank=True)
    source_journal_name = models.CharField(max_length=255, blank=True)
    publication_date = models.DateField(blank=True, null=True)
    publication_month = models.DateField(blank=True, null=True)
    article_url = models.URLField(max_length=500, blank=True)
    pubmed_url = models.URLField(max_length=500, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    # Editorial fields (merged from submissions.Article)
    tags_string = models.TextField(blank=True, default="")
    journal = models.ForeignKey(
        "submissions.Journal", on_delete=models.SET_NULL, null=True, blank=True, related_name="articles"
    )
    citation = models.TextField(blank=True, default="")
    active = models.BooleanField(default=False)
    recommendation_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ("-publication_date", "-created")
        indexes = [
            models.Index(fields=["pmid"], name="backend_pa_pmid_idx"),
        ]

    def save(self, *args, **kwargs):
        doi = (self.doi or "").strip().lower()
        self.doi = doi or None
        # Normalise empty pmid to None for unique constraint
        if not self.pmid:
            self.pmid = None
        super().save(*args, **kwargs)
        if self.tags_string:
            current_tags = self.create_tag_objects()
            self.prune_tag_objects(current_tags)

    def __str__(self):
        return self.title or f"PMID {self.pmid}"

    # ── Compatibility properties (for templates using old Article field names) ──

    @property
    def name(self):
        return self.title

    @property
    def url(self):
        return self.article_url

    @property
    def year(self):
        return self.publication_date.year if self.publication_date else None

    # ── Title / subtitle helpers (ported from submissions.Article) ──

    def get_title(self):
        separators = [":", " - "]
        for sep in separators:
            if sep in self.title:
                return self.title.split(sep, 1)[0].strip()
        return self.title

    def get_subtitle(self):
        separators = [":", "-"]
        for sep in separators:
            if sep in self.title:
                return self.title.split(sep, 1)[1].strip()
        return ""

    def get_truncated_name(self):
        from spanza_journal_watch.utils.functions import shorten_text

        return shorten_text(self.title, self.TRUNCATED_NAME_LENGTH)

    def get_related_review(self):
        return self.reviews.exclude(active=False).order_by("-created").first()

    # ── Tag management (ported from submissions.Article) ──

    def tags_list(self):
        from django.utils.text import slugify

        hashtag_list = []
        for word in self.tags_string.split(" "):
            if slugify(word) and word[0] == "#":
                hashtag_list.append(slugify(word[:255]))
        return list(set(hashtag_list))

    def create_tag_objects(self):
        from spanza_journal_watch.submissions.models import Tag

        current_tags = []
        for text in self.tags_list():
            try:
                tag = Tag.objects.get(text=text)
            except Tag.DoesNotExist:
                tag = Tag(text=text)
                tag.save()
            except Tag.MultipleObjectsReturned:
                continue
            current_tags.append(tag)
            tag.articles.add(self)
        return current_tags

    def prune_tag_objects(self, current_tags):
        tags = self.tags.all()
        for tag in tags:
            if tag not in current_tags:
                tag.articles.remove(self)
                tag.delete_if_orphaned()


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


def can_recommend_pubmed_articles(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm("submissions.can_recommend")
