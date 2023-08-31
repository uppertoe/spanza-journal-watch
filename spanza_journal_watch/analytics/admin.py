from django.contrib import admin

from . import models


@admin.register(models.NewsletterOpen)
class NewsletterOpenAdmin(admin.ModelAdmin):
    list_display = ("email_address", "newsletter")


@admin.register(models.NewsletterClick)
class NewsletterClickAdmin(admin.ModelAdmin):
    list_display = ("email_address", "newsletter")


@admin.register(models.PageView)
class PageViewAdmin(admin.ModelAdmin):
    list_display = ("content_object", "timestamp")
