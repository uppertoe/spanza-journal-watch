from django.urls import path

from . import views

app_name = "submissions"
urlpatterns = [
    path("review/", views.ReviewListView.as_view(), name="review_list"),
    path("review/<slug:slug>", views.ReviewDetailView.as_view(), name="review_detail"),
    path("issue/", views.IssueListView.as_view(), name="issue_list"),
    path("issue/<slug:slug>", views.IssueDetailView.as_view(), name="issue_detail"),
    path("issues/latest", views.LatestIssueView.as_view(), name="issue_latest"),
    path("tags/", views.TagListView.as_view(), name="tag_list"),
    path("tags/<slug:slug>", views.TagDetailView.as_view(), name="tag_detail"),
]
