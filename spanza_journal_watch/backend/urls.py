from django.urls import path

from . import views

app_name = "backend"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("issues/builder", views.issue_builder, name="issue_builder"),
    path("issues/builder/save", views.save_issue_draft, name="save_issue_draft"),
    path("issues/builder/<int:issue_id>/save", views.save_issue_draft, name="update_issue_draft"),
    path("issues/builder/<int:issue_id>/reviews/new", views.new_review_form, name="new_issue_review_form"),
    path("issues/builder/<int:issue_id>/reviews/add", views.add_issue_review, name="add_issue_review"),
    path(
        "issues/builder/<int:issue_id>/reviews/<int:review_id>/edit",
        views.edit_issue_review_form,
        name="edit_issue_review_form",
    ),
    path(
        "issues/builder/<int:issue_id>/reviews/<int:review_id>/update",
        views.update_issue_review,
        name="update_issue_review",
    ),
    path(
        "issues/builder/<int:issue_id>/reviews/<int:review_id>/remove",
        views.remove_issue_review,
        name="remove_issue_review",
    ),
    path("issues/builder/<int:issue_id>/publish", views.publish_issue_bundle, name="publish_issue_bundle"),
    path("subscribers/upload", views.upload_subscriber_csv, name="upload_subscribers"),
    path("subscribers/upload/change-header/<str:save_token>", views.edit_csv_header, name="edit_csv_header"),
    path("subscribers/upload/process-csv/<str:save_token>", views.process_csv, name="process_csv"),
    path("newsletter/release", views.newsletter_release_list, name="newsletter_release_list"),
    path("newsletter/release/create", views.create_newsletter, name="create_newsletter"),
    path("newsletter/send/<str:send_token>", views.final_newsletter, name="final_newsletter"),
    path("newsletter/send/test/<str:send_token>", views.send_test_newsletter, name="send_test_newsletter"),
    path(
        "newsletter/send/enable-resend/<str:send_token>",
        views.enable_newsletter_resend,
        name="enable_newsletter_resend",
    ),
    path("newsletter/send/confirm/<str:send_token>", views.send_final_newsletter, name="send_final_newsletter"),
    path("newsletter/stats", views.newsletter_stats_list, name="newsletter_stats_list"),
    path("newsletter/stats/<int:pk>", views.newsletter_stats_detail, name="newsletter_stats_detail"),
]
