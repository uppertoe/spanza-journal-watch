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


@admin.register(models.PageView)
class PageViewAdmin(admin.ModelAdmin):
    list_display = ("subscriber", "content_object", "timestamp")

    empty_value_display = "-anonymous-"
