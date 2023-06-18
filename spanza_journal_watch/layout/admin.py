from django.contrib import admin

from .models import FeatureArticle, Homepage


@admin.register(Homepage)
class HomepageAdmin(admin.ModelAdmin):
    list_display = ("issue",)


@admin.register(FeatureArticle)
class FeatureArticleAdmin(admin.ModelAdmin):
    list_display = ("title",)
