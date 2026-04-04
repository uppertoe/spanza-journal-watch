from django.urls import path
from django.views.generic.base import RedirectView

from . import views

app_name = "submissions"
urlpatterns = [
    path("reviews", RedirectView.as_view(url="search", permanent=False), name="review_list"),  # Redirect to search
    path("reviews/<slug:slug>", views.ReviewDetailView.as_view(), name="review_detail"),
    path("journals", views.JournalListView.as_view(), name="journal_list"),
    path(
        "journals/articles/<int:article_id>/star",
        views.journal_article_toggle_star,
        name="journal_article_toggle_star",
    ),
    path(
        "journals/articles/<int:article_id>/recommend",
        views.journal_article_toggle_recommend,
        name="journal_article_toggle_recommend",
    ),
    path(
        "journals/articles/<int:article_id>/mark-read",
        views.journal_article_mark_fulltext,
        name="journal_article_mark_fulltext",
    ),
    path("journals/fulltext-ids/", views.journal_fulltext_ids, name="journal_fulltext_ids"),
    path("journals/search/", views.journal_search, name="journal_search"),
    path("journals/reading-list/", views.journal_reading_list, name="journal_reading_list"),
    path(
        "journals/articles/<int:article_id>/archive",
        views.journal_article_toggle_archive,
        name="journal_article_toggle_archive",
    ),
    path("issues", views.IssueListView.as_view(), name="issue_list"),
    path("issues/latest", views.LatestIssueView.as_view(), name="issue_latest"),
    path("issues/<slug:slug>", views.IssueDetailView.as_view(), name="issue_detail"),
    path("explore", views.TagListView.as_view(), name="tag_list"),
    path("explore/collections/<slug:slug>", views.CuratedCollectionDetailView.as_view(), name="collection_detail"),
    path("explore/<slug:slug>", views.TagDetailView.as_view(), name="tag_detail"),
    # Redirect old /tags URLs
    path("tags", RedirectView.as_view(url="/explore", permanent=True)),
    path("tags/<slug:slug>", RedirectView.as_view(pattern_name="submissions:tag_detail", permanent=True)),
    path("search", views.SearchView.as_view(), name="search"),
    path("ajax/tags", views.ajax_get_tags, name="ajax_get_tags"),  # Hardcoded in base.html
    path("about", views.HealthServiceListView.as_view(), name="about"),
    path("about/<slug:slug>", views.AuthorDetailView.as_view(), name="author_detail"),
]
