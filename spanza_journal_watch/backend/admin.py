from django.contrib import admin

from . import models


@admin.register(models.SubscriberCSV)
class SubscriberCSVAdmin(admin.ModelAdmin):
    list_display = ("name", "created")
