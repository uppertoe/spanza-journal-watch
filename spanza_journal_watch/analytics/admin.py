from django.contrib import admin

from . import models


@admin.register(models.NewsletterOpen)
class NewsletterOpenAdmin(admin.ModelAdmin):
    list_display = ("subscriber", "newsletter", "timestamp")
    list_filter = ("newsletter",)


@admin.register(models.NewsletterClick)
class NewsletterClickAdmin(admin.ModelAdmin):
    list_display = ("subscriber", "newsletter", "timestamp")
    list_filter = ("newsletter",)


@admin.register(models.AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "content_object", "source", "automated", "timestamp")
    list_filter = ("event_type", "automated", "source")
    search_fields = ("user_agent", "session_key")

    empty_value_display = "-anonymous-"
