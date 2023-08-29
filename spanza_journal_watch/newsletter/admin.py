from django.contrib import admin

from . import models


@admin.register(models.Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "subscribed")


@admin.register(models.Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ("subject", "ready_to_send", "is_sent")


@admin.register(models.Logo)
class LogoAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(models.ElementImage)
class ElementImageAdmin(admin.ModelAdmin):
    list_display = ("name", "type")
