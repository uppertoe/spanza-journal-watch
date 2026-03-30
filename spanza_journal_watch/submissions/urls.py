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
    path("issues", views.IssueListView.as_view(), name="issue_list"),
    path("issues/latest", views.LatestIssueView.as_view(), name="issue_latest"),
    path("issues/<slug:slug>", views.IssueDetailView.as_view(), name="issue_detail"),
    path("tags", views.TagListView.as_view(), name="tag_list"),
    path("tags/<slug:slug>", views.TagDetailView.as_view(), name="tag_detail"),
    path("search", views.SearchView.as_view(), name="search"),
    path("ajax/tags", views.ajax_get_tags, name="ajax_get_tags"),  # Hardcoded in base.html
    path("about", views.HealthServiceListView.as_view(), name="about"),
    path("about/<slug:slug>", views.AuthorDetailView.as_view(), name="author_detail"),
]
