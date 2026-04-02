from django.contrib import admin

from spanza_journal_watch.cpd.models import CPDReport


@admin.register(CPDReport)
class CPDReportAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "date_from", "date_to", "article_count", "created")
    list_filter = ("status",)
    raw_id_fields = ("user",)
