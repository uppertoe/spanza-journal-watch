from django.contrib import admin

from . import models


@admin.register(models.Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "subscribed", "tester", "bounced", "complained", "created")
    list_filter = ("subscribed", "tester", "bounced", "complained")
    search_fields = ("email",)
    date_hierarchy = "created"
    readonly_fields = ("created", "modified", "unsubscribe_token")


@admin.register(models.Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ("subject", "issue", "send_date", "ready_to_send", "is_test_sent", "is_sent", "emails_sent")
    list_filter = ("ready_to_send", "is_test_sent", "is_sent")
    search_fields = ("subject", "issue__name")
    date_hierarchy = "send_date"
    autocomplete_fields = ["issue"]
    readonly_fields = ("emails_sent", "email_token", "header_image_processed")
