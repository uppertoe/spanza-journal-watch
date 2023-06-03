from django.urls import path

from . import views

urlpatterns = [
    path("reviews/", views.ReviewListView.as_view(), name="review-list"),
    path("review/<slug:slug>", views.ReviewDetailView.as_view(), name="review-detail"),
    path("issues/", views.IssueListView.as_view(), name="issue-list"),
    path("issues/<slug:slug>", views.IssueDetailView.as_view(), name="issue-detail"),
    path("tags/", views.TagListView.as_view(), name="tag-list"),
    path("tags/<slug:slug>", views.TagDetailView.as_view(), name="tag-detail"),
]
