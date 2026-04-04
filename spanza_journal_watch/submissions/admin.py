from django.contrib import admin
from markdownx.admin import MarkdownxModelAdmin

from . import models


@admin.register(models.HealthService)
class HealthServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "logo_authorised")
    list_filter = ("logo_authorised",)
    search_fields = ("name",)


@admin.register(models.Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "title", "email", "anonymous", "show_profile_image")
    list_filter = ("anonymous", "show_profile_image")
    search_fields = ("name", "email")
    filter_horizontal = ("health_services",)
    autocomplete_fields = ["user"]
    readonly_fields = ("slug",)


@admin.register(models.Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("text", "curated", "display_order", "active")
    list_filter = ("active", "curated")
    search_fields = ("text",)
    readonly_fields = ("slug",)


@admin.register(models.MeshTagMapping)
class MeshTagMappingAdmin(admin.ModelAdmin):
    list_display = ("mesh_term", "tag")
    list_filter = ("tag",)
    search_fields = ("mesh_term", "tag__text")
    autocomplete_fields = ["tag"]


@admin.register(models.CuratedCollection)
class CuratedCollectionAdmin(admin.ModelAdmin):
    list_display = ("title", "active", "display_order", "created")
    list_filter = ("active",)
    search_fields = ("title",)
    filter_horizontal = ("tags", "reviews")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(models.Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "active", "url")
    list_filter = ("active",)
    search_fields = ("name", "abbreviation")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(models.Review)
class ReviewAdmin(MarkdownxModelAdmin):
    list_display = ("article", "author", "active", "is_featured", "publish_date", "created")
    list_filter = ("active", "is_featured")
    search_fields = ("article__title", "author__name")
    autocomplete_fields = ["article", "author"]
    date_hierarchy = "created"


@admin.register(models.Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "active")
    list_filter = ("active",)
    search_fields = ("name",)
    date_hierarchy = "date"
    autocomplete_fields = ["reviews"]
    readonly_fields = ("slug",)

    def get_prepopulated_fields(self, request, obj=None):
        # Allow superusers to regenerate the slug; for everyone else it is readonly.
        return {"slug": ("name",)} if request.user.is_superuser else {}

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        return ("slug",)


@admin.register(models.Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("article", "author", "created")
    search_fields = ("article__title", "author__email")
    autocomplete_fields = ["article"]
    readonly_fields = ("author",)

    def get_readonly_fields(self, request, obj=None):
        # Allow users with Author change permission to reassign comment authorship.
        if request.user.has_perm("submissions.change_author"):
            return ()
        return ("author",)


@admin.register(models.Hit)
class HitAdmin(admin.ModelAdmin):
    list_display = ("content_object", "count", "last_accessed")
    readonly_fields = ("content_type", "object_id", "count", "last_accessed")
