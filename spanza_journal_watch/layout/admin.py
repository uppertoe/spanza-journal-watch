from django.contrib import admin
from django.core.cache import cache

from .models import HOMEPAGE_CACHE_KEY, FeatureArticle, Homepage, PageHeader


@admin.register(FeatureArticle)
class FeatureArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "created", "modified")
    search_fields = ("title",)
    readonly_fields = ("slug", "created", "modified")
    date_hierarchy = "created"


@admin.register(Homepage)
class HomepageAdmin(admin.ModelAdmin):
    list_display = ("issue", "publication_ready", "is_current", "created")
    list_filter = ("publication_ready",)
    search_fields = ("issue__name",)
    autocomplete_fields = ["issue"]
    readonly_fields = ("created", "modified")
    date_hierarchy = "created"
    actions = ["make_current_homepage"]

    @admin.display(boolean=True, description="Current")
    def is_current(self, obj):
        return cache.get(HOMEPAGE_CACHE_KEY) == obj.pk

    @admin.action(description="Set as current homepage")
    def make_current_homepage(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one homepage.", level="error")
            return
        homepage = queryset.first()
        if not homepage.publication_ready:
            self.message_user(request, "Homepage must have publication_ready=True first.", level="error")
            return
        Homepage.publish_homepage(homepage)
        self.message_user(request, f"Current homepage set to: {homepage}")


@admin.register(PageHeader)
class PageHeaderAdmin(admin.ModelAdmin):
    list_display = ("page_type", "feature_article", "active", "modified")
    list_filter = ("page_type", "active")
    search_fields = ("feature_article__title",)
    autocomplete_fields = ["feature_article"]
    readonly_fields = ("created", "modified")
