from django.contrib import admin

from . import models


@admin.register(models.Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "subscribed", "bounced", "complained")


@admin.register(models.Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ("subject", "ready_to_send", "is_test_sent", "is_sent")


@admin.register(models.ElementImage)
class ElementImageAdmin(admin.ModelAdmin):
    list_display = ("name", "type")
