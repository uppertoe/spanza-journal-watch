from django.urls import path
from django.views.generic.base import RedirectView

from . import views

app_name = "submissions"
urlpatterns = [
    # TODO redirect reviews/ to a searchview
    path("reviews", RedirectView.as_view(url="search", permanent=False), name="review_list"),  # Redirect to search
    path("reviews/<slug:slug>", views.ReviewDetailView.as_view(), name="review_detail"),
    path("issues", views.IssueListView.as_view(), name="issue_list"),
    path("issues/latest", views.LatestIssueView.as_view(), name="issue_latest"),
    path("issues/<slug:slug>", views.IssueDetailView.as_view(), name="issue_detail"),
    path("tags", views.TagListView.as_view(), name="tag_list"),
    path("tags/<slug:slug>", views.TagDetailView.as_view(), name="tag_detail"),
    path("search", views.SearchView.as_view(), name="search"),
    path("ajax/tags", views.ajax_get_tags, name="ajax_get_tags"),
]
