from django.contrib import admin

from .models import (
    FeatureArticle,
    Gradient,
    Homepage,
    HomepagePage,
    IssueDetailPage,
    IssuePage,
    ReviewPage,
    SearchPage,
    TagPage,
)


# Inlines
class FeatureInline(admin.TabularInline):
    model = FeatureArticle
    extra = 1


class HomepageInline(admin.TabularInline):
    model = Homepage
    extra = 1


# ModelAdmins


@admin.register(Homepage)
class HomepageAdmin(admin.ModelAdmin):
    list_display = ("issue",)


@admin.register(FeatureArticle)
class FeatureArticleAdmin(admin.ModelAdmin):
    list_display = ("title",)


@admin.register(SearchPage)
class SearchPageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")


@admin.register(IssuePage)
class IssuePageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")


@admin.register(ReviewPage)
class ReviewPageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")


@admin.register(TagPage)
class TagPageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")


@admin.register(IssueDetailPage)
class IssueDetailPageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")


@admin.register(HomepagePage)
class HomepagePageAdmin(admin.ModelAdmin):
    list_display = ("feature_article", "active")
    inlines = [HomepageInline]


@admin.register(Gradient)
class GradientAdmin(admin.ModelAdmin):
    list_display = ("name",)
