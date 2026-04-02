from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from . import models

# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


class InboundEmailInline(admin.TabularInline):
    model = models.InboundEmail
    extra = 0
    fields = ("sender", "subject", "sent_timestamp", "read")
    readonly_fields = ("sender", "subject", "sent_timestamp")
    ordering = ("sent_timestamp",)
    show_change_link = True


class SentEmailInline(admin.TabularInline):
    model = models.SentEmail
    extra = 0
    fields = ("sent_by", "subject", "created")
    readonly_fields = ("sent_by", "subject", "created")
    ordering = ("created",)
    show_change_link = True


@admin.register(models.EmailThread)
class EmailThreadAdmin(admin.ModelAdmin):
    list_display = ("external_address", "subject", "has_unread", "last_message_at", "created")
    list_filter = ("has_unread",)
    search_fields = ("external_address", "subject")
    readonly_fields = ("created", "last_message_at")
    date_hierarchy = "last_message_at"
    inlines = [InboundEmailInline, SentEmailInline]


@admin.register(models.InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("sender", "subject", "thread", "read", "sent_timestamp")
    list_filter = ("read",)
    search_fields = ("sender", "subject", "thread__external_address")
    readonly_fields = (
        "sender",
        "recipient",
        "subject",
        "body",
        "message_id",
        "in_reply_to",
        "sent_timestamp",
        "created",
    )
    raw_id_fields = ("thread",)
    date_hierarchy = "sent_timestamp"


@admin.register(models.SentEmail)
class SentEmailAdmin(admin.ModelAdmin):
    list_display = ("recipient", "subject", "sent_by", "created")
    search_fields = ("recipient", "subject", "thread__external_address")
    readonly_fields = ("recipient", "subject", "body", "message_id", "sent_by", "created")
    raw_id_fields = ("thread",)
    date_hierarchy = "created"


# ---------------------------------------------------------------------------
# Subscribers / CSV
# ---------------------------------------------------------------------------


@admin.register(models.SubscriberCSV)
class SubscriberCSVAdmin(admin.ModelAdmin):
    list_display = ("name", "confirmed", "processed", "row_count", "email_added_count", "created")
    list_filter = ("confirmed", "processed")
    search_fields = ("name",)
    readonly_fields = ("created", "modified", "row_count", "email_added_count", "save_token")


# ---------------------------------------------------------------------------
# Planka
# ---------------------------------------------------------------------------


@admin.register(models.PlankaIssueBinding)
class PlankaIssueBindingAdmin(admin.ModelAdmin):
    list_display = ("issue", "project_name", "project_id", "board_id", "instructions_board_id", "modified")
    search_fields = ("issue__name", "project_name", "project_id", "board_id")
    autocomplete_fields = ["issue"]
    readonly_fields = ("created", "modified")


@admin.register(models.PlankaCardImport)
class PlankaCardImportAdmin(admin.ModelAdmin):
    list_display = ("card_name", "card_id", "issue", "review", "imported_by", "created")
    search_fields = ("card_name", "card_id", "issue__name")
    autocomplete_fields = ["issue"]
    readonly_fields = ("created",)
    date_hierarchy = "created"


@admin.register(models.PlankaIntegrationCredential)
class PlankaIntegrationCredentialAdmin(admin.ModelAdmin):
    list_display = ("auth_mode", "api_key_prefix", "configured_by", "last_validated_at", "modified")
    readonly_fields = ("singleton", "api_key_prefix", "created", "modified", "last_validated_at")
    exclude = ("api_key",)

    def changelist_view(self, request, extra_context=None):
        obj = models.PlankaIntegrationCredential.objects.first()
        if obj:
            return HttpResponseRedirect(reverse("admin:backend_plankaintegrationcredential_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(models.PlankaBoardBackgroundAsset)
class PlankaBoardBackgroundAssetAdmin(admin.ModelAdmin):
    list_display = ("name", "uploaded_by", "created", "modified")
    search_fields = ("name",)
    readonly_fields = ("created", "modified")


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------


@admin.register(models.PubmedIntegrationCredential)
class PubmedIntegrationCredentialAdmin(admin.ModelAdmin):
    list_display = ("configured_by", "last_validated_at", "modified")
    readonly_fields = ("singleton", "created", "modified", "last_validated_at")
    exclude = ("api_key",)

    def changelist_view(self, request, extra_context=None):
        obj = models.PubmedIntegrationCredential.objects.first()
        if obj:
            return HttpResponseRedirect(reverse("admin:backend_pubmedintegrationcredential_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(models.PubmedImportBatch)
class PubmedImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "issue", "from_month", "to_month", "result_count", "selected_count", "created")
    search_fields = ("issue__name", "keyword_query")
    autocomplete_fields = ["issue"]
    readonly_fields = ("created",)
    date_hierarchy = "created"


@admin.register(models.PubmedArticle)
class PubmedArticleAdmin(admin.ModelAdmin):
    list_display = ("pmid", "doi", "source_journal_name", "publication_date", "modified")
    search_fields = ("pmid", "doi", "title", "source_journal_name")
    readonly_fields = ("modified",)
    date_hierarchy = "publication_date"


@admin.register(models.PubmedBatchArticle)
class PubmedBatchArticleAdmin(admin.ModelAdmin):
    list_display = ("batch", "article", "issue", "watched_journal", "is_selected")
    list_filter = ("is_selected", "watched_journal")
    search_fields = ("article__pmid", "article__doi", "article__title", "issue__name")
    autocomplete_fields = ["issue"]


# ---------------------------------------------------------------------------
# Watched journals
# ---------------------------------------------------------------------------


@admin.register(models.WatchedJournal)
class WatchedJournalAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "journal",
        "issn_print",
        "issn_electronic",
        "source",
        "active",
        "visible_on_frontend",
        "modified",
    )
    list_filter = ("active", "source")
    search_fields = ("name", "issn_print", "issn_electronic", "journal__name")
    autocomplete_fields = ["journal"]
    readonly_fields = ("modified",)


# ---------------------------------------------------------------------------
# Backend preferences
# ---------------------------------------------------------------------------


@admin.register(models.BackendPreference)
class BackendPreferenceAdmin(admin.ModelAdmin):
    filter_horizontal = ("default_watched_journals",)
    readonly_fields = ("singleton", "created", "modified")

    def changelist_view(self, request, extra_context=None):
        obj = models.BackendPreference.objects.first()
        if obj:
            return HttpResponseRedirect(reverse("admin:backend_backendpreference_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


# ---------------------------------------------------------------------------
# Issue contributors
# ---------------------------------------------------------------------------


@admin.register(models.IssueContributor)
class IssueContributorAdmin(admin.ModelAdmin):
    list_display = ("issue", "email", "role", "status", "user", "invited_at", "accepted_at")
    list_filter = ("role", "status")
    search_fields = ("issue__name", "email", "user__email")
    autocomplete_fields = ["issue", "user"]
    readonly_fields = ("invited_at", "accepted_at", "modified")
    date_hierarchy = "invited_at"


@admin.register(models.IssueContributorInvite)
class IssueContributorInviteAdmin(admin.ModelAdmin):
    list_display = ("contributor", "expires_at", "is_consumed", "sent_at", "created_by")
    list_filter = ("sent_at",)
    search_fields = ("contributor__email", "contributor__issue__name")
    readonly_fields = ("token_hash", "consumed_at", "sent_at", "modified")
    date_hierarchy = "expires_at"

    @admin.display(boolean=True, description="Consumed")
    def is_consumed(self, obj):
        return obj.consumed_at is not None
