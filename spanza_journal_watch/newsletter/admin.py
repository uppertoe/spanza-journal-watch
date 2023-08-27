from django.contrib import admin

from . import models


@admin.register(models.Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "subscribed")


@admin.register(models.Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ("subject", "ready_to_send", "is_sent")


@admin.register(models.EmailImage)
class EmailImageAdmin(admin.ModelAdmin):
    list_display = ("name", "type")


@admin.register(models.EmailFont)
class EmailFontAdmin(admin.ModelAdmin):
    list_display = ("name", "type")
