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
