from django.contrib import admin

from .models import FeatureArticle, Homepage, PageHeader


# Inlines
class FeatureInline(admin.TabularInline):
    model = FeatureArticle
    extra = 1


# ModelAdmins


@admin.register(Homepage)
class HomepageAdmin(admin.ModelAdmin):
    list_display = ("issue",)


@admin.register(FeatureArticle)
class FeatureArticleAdmin(admin.ModelAdmin):
    list_display = ("title",)


@admin.register(PageHeader)
class PageHeaderAdmin(admin.ModelAdmin):
    list_display = ("page_type", "feature_article", "active")
