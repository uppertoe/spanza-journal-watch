from django.urls import path

from . import views

app_name = "backend"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("articles/watched-journals", views.watched_journals, name="watched_journals"),
    path("articles/watched-journals/search", views.watched_journal_search, name="watched_journal_search"),
    path(
        "articles/watched-journals/<int:watched_journal_id>/toggle-active",
        views.watched_journal_toggle_active,
        name="watched_journal_toggle_active",
    ),
    path("articles/intake", views.article_intake, name="article_intake"),
    path("articles/intake/pubmed-api-key", views.pubmed_save_api_key, name="pubmed_save_api_key"),
    path("articles/intake/<int:batch_id>/results", views.article_intake_results, name="article_intake_results"),
    path(
        "articles/intake/<int:batch_id>/select/<int:item_id>",
        views.article_intake_toggle_selection,
        name="article_intake_toggle_selection",
    ),
    path(
        "articles/intake/<int:batch_id>/bulk-select",
        views.article_intake_bulk_selection,
        name="article_intake_bulk_selection",
    ),
    path(
        "articles/intake/<int:batch_id>/assign-issue",
        views.article_intake_assign_issue,
        name="article_intake_assign_issue",
    ),
    path(
        "articles/intake/<int:batch_id>/refresh",
        views.article_intake_refresh_batch,
        name="article_intake_refresh_batch",
    ),
    path(
        "articles/intake/<int:batch_id>/push-to-planka",
        views.article_intake_push_to_planka,
        name="article_intake_push_to_planka",
    ),
    path(
        "articles/intake/<int:batch_id>/reconcile-planka-status",
        views.article_intake_reconcile_planka_status,
        name="article_intake_reconcile_planka_status",
    ),
    path(
        "articles/intake/<int:batch_id>/task-status",
        views.article_intake_task_status,
        name="article_intake_task_status",
    ),
    path("issues/builder", views.issue_builder, name="issue_builder"),
    path("issues/planka", views.issue_planka_import, name="issue_planka_import"),
    path("issues/planka/api-key", views.planka_save_api_key, name="planka_save_api_key"),
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
    path(
        "issues/builder/<int:issue_id>/planka/setup",
        views.planka_setup_issue_project,
        name="planka_setup_issue_project",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/recreate-board",
        views.planka_recreate_issue_board,
        name="planka_recreate_issue_board",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/publish-cards",
        views.planka_refresh_publish_cards,
        name="planka_refresh_publish_cards",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/background",
        views.planka_update_project_background,
        name="planka_update_project_background",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/project-name",
        views.planka_update_project_name,
        name="planka_update_project_name",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/import-card",
        views.planka_import_publish_card,
        name="planka_import_publish_card",
    ),
    path(
        "issues/builder/<int:issue_id>/planka/import-cards",
        views.planka_import_publish_cards_bulk,
        name="planka_import_publish_cards_bulk",
    ),
    path("subscribers/upload", views.upload_subscriber_csv, name="upload_subscribers"),
    path("subscribers/list", views.subscriber_list, name="subscriber_list"),
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
