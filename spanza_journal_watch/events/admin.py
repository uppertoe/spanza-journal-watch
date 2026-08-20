from django.contrib import admin
from markdownx.admin import MarkdownxModelAdmin

from . import models


class LiveSessionReadingInline(admin.StackedInline):
    model = models.LiveSessionReading
    extra = 0
    autocomplete_fields = ["review", "article"]
    fields = (
        "display_order",
        "topic",
        "prompt",
        "review",
        "article",
        "title",
        "source",
        "url",
        "pubmed_url",
        "review_url",
    )


@admin.register(models.LiveSession)
class LiveSessionAdmin(MarkdownxModelAdmin):
    list_display = ("title", "start_datetime", "speaker", "chair", "active", "display_order")
    list_filter = ("active",)
    search_fields = ("title", "speaker", "chair")
    readonly_fields = ("slug",)
    inlines = [LiveSessionReadingInline]
