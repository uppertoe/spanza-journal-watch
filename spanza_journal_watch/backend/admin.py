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
    list_display = ("issue", "project_name", "project_id", "board_id", "modified")
    search_fields = ("issue__name", "project_name", "project_id", "board_id")


@admin.register(models.PlankaCardImport)
class PlankaCardImportAdmin(admin.ModelAdmin):
    list_display = ("card_name", "card_id", "issue", "review", "imported_by", "created")
    search_fields = ("card_name", "card_id", "issue__name")
