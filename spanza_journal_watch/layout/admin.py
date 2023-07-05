from django.contrib import admin

from .models import FeatureArticle, Homepage, IssuePage, ReviewPage, SearchPage, TagPage


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
