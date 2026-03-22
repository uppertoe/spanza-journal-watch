from django.contrib import admin

from . import models


@admin.register(models.SubscriberCSV)
class SubscriberCSVAdmin(admin.ModelAdmin):
    list_display = ("name", "created")


@admin.register(models.InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("sender", "subject", "created")


@admin.register(models.PlankaIssueBinding)
class PlankaIssueBindingAdmin(admin.ModelAdmin):
    list_display = (
        "issue",
        "project_name",
        "project_id",
        "board_id",
        "instructions_board_id",
        "background_asset",
        "modified",
    )
    search_fields = ("issue__name", "project_name", "project_id", "board_id", "instructions_board_id")


@admin.register(models.PlankaCardImport)
class PlankaCardImportAdmin(admin.ModelAdmin):
    list_display = ("card_name", "card_id", "issue", "review", "imported_by", "created")
    search_fields = ("card_name", "card_id", "issue__name")


@admin.register(models.PlankaIntegrationCredential)
class PlankaIntegrationCredentialAdmin(admin.ModelAdmin):
    list_display = ("auth_mode", "api_key_prefix", "configured_by", "last_validated_at", "modified")
    exclude = ("api_key",)
    readonly_fields = ("singleton", "created", "modified", "last_validated_at")


@admin.register(models.PlankaBoardBackgroundAsset)
class PlankaBoardBackgroundAssetAdmin(admin.ModelAdmin):
    list_display = ("name", "uploaded_by", "created", "modified")
    search_fields = ("name",)


@admin.register(models.PubmedIntegrationCredential)
class PubmedIntegrationCredentialAdmin(admin.ModelAdmin):
    list_display = ("configured_by", "last_validated_at", "modified")
    exclude = ("api_key",)
    readonly_fields = ("singleton", "created", "modified", "last_validated_at")


@admin.register(models.BackendPreference)
class BackendPreferenceAdmin(admin.ModelAdmin):
    list_display = ("singleton", "modified")
    filter_horizontal = ("default_watched_journals",)
    readonly_fields = ("singleton", "created", "modified")


@admin.register(models.WatchedJournal)
class WatchedJournalAdmin(admin.ModelAdmin):
    list_display = ("name", "journal", "issn_print", "issn_electronic", "active", "modified")
    list_filter = ("active",)
    search_fields = ("name", "issn_print", "issn_electronic", "journal__name")


@admin.register(models.PubmedImportBatch)
class PubmedImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "issue", "from_month", "to_month", "result_count", "selected_count", "created")
    search_fields = ("issue__name", "keyword_query")


@admin.register(models.PubmedArticle)
class PubmedArticleAdmin(admin.ModelAdmin):
    list_display = ("pmid", "doi", "source_journal_name", "publication_date", "modified")
    search_fields = ("pmid", "doi", "title", "source_journal_name")


@admin.register(models.PubmedBatchArticle)
class PubmedBatchArticleAdmin(admin.ModelAdmin):
    list_display = ("batch", "article", "issue", "watched_journal", "is_selected", "modified")
    list_filter = ("is_selected", "watched_journal")
    search_fields = ("article__pmid", "article__doi", "article__title", "issue__name")


@admin.register(models.IssueContributor)
class IssueContributorAdmin(admin.ModelAdmin):
    list_display = ("issue", "email", "role", "status", "user", "invited_at", "accepted_at", "modified")
    list_filter = ("role", "status")
    search_fields = ("issue__name", "email", "user__email")


@admin.register(models.IssueContributorInvite)
class IssueContributorInviteAdmin(admin.ModelAdmin):
    list_display = ("contributor", "expires_at", "consumed_at", "created_by", "sent_at", "modified")
    list_filter = ("consumed_at",)
    search_fields = ("contributor__email", "contributor__issue__name")
