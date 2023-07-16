from django.contrib import admin

from . import models


class SaveAuthorMixin:
    """Overrides save_model to set author field to request.user"""

    def save_model(self, request, obj, form, change):
        if obj.author is None:
            obj.author = request.user
        super().save_model(request, obj, form, change)


@admin.register(models.HealthService)
class HealthServieAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(models.Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(models.Hit)
class HitAdmin(admin.ModelAdmin):
    list_display = ("content_object", "count")


class JournalAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name", "abbreviation")
    prepopulated_fields = {"slug": ("name",)}


class ArticleAdmin(admin.ModelAdmin):
    list_display = ("name", "journal", "year")
    search_fields = ("name", "journal")


class ReviewAdmin(SaveAuthorMixin, admin.ModelAdmin):
    list_display = ("article", "author")
    list_filter = ("author",)
    search_fields = ("article",)
    readonly_fields = ("author",)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if request.user.has_perm("accounts.change_author"):
            readonly_fields.remove("author")
        return readonly_fields


class IssueAdmin(admin.ModelAdmin):
    list_display = ("name", "date")
    search_fields = ("name", "reviews")
    readonly_fields = ("slug",)
    prepopulated_fields = {"slug": ("name",)}

    def get_prepopulated_fields(self, request, obj=None):
        if request.user.is_superuser:
            return self.prepopulated_fields
        return {}

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if request.user.is_superuser:
            readonly_fields.remove("slug")
        return readonly_fields


class CommentAdmin(admin.ModelAdmin):
    list_display = ("article", "author")
    list_filter = ("author",)
    search_fields = ("article",)
    readonly_fields = ("author",)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if request.user.has_perm("accounts.change_author"):
            readonly_fields.remove("author")
        return readonly_fields


admin.site.register(models.Journal, JournalAdmin)
admin.site.register(models.Article, ArticleAdmin)
admin.site.register(models.Review, ReviewAdmin)
admin.site.register(models.Issue, IssueAdmin)
admin.site.register(models.Comment, CommentAdmin)
admin.site.register(models.Tag)
