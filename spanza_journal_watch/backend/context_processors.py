from django.conf import settings

from spanza_journal_watch.submissions.models import Issue

ISSUE_PAGE_URL_NAMES = frozenset(
    [
        "issue_builder",
        "save_issue_draft",
        "update_issue_draft",
        "article_intake",
        "article_intake_results",
        "article_intake_task_status",
        "article_intake_toggle_selection",
        "article_intake_bulk_selection",
        "article_intake_assign_issue",
        "article_intake_refresh_batch",
        "article_intake_push_to_planka",
        "article_intake_reconcile_planka_status",
        "issue_reviewers",
        "issue_add_contributor",
        "issue_send_contributor_invites",
        "issue_resend_contributor_invite",
        "issue_revoke_contributor",
        "issue_sync_contributor_planka",
        "issue_planka_import",
        "planka_setup_issue_project",
        "planka_refresh_publish_cards",
        "planka_import_publish_card",
        "planka_update_project_background",
        "planka_update_project_name",
        "issue_reviews_edit",
        "new_issue_review_form",
        "add_issue_review",
        "edit_issue_review_form",
        "update_issue_review",
        "remove_issue_review",
        "issue_publish",
        "issue_set_homepage",
        "toggle_review_active",
        "newsletter_release_list",
        "create_newsletter",
        "final_newsletter",
        "send_final_newsletter",
        "send_test_newsletter",
        "newsletter_stats_list",
        "newsletter_stats_detail",
    ]
)


def selected_issue(request):
    """Inject the session-persisted selected issue and issue list into every template context."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}

    is_coordinator_only = request.user.has_perm("submissions.regional_coordinator") and not request.user.has_perm(
        "submissions.chief_editor"
    )

    issues = list(Issue.objects.only("pk", "name", "date", "active").order_by("-modified"))

    if is_coordinator_only:
        from spanza_journal_watch.backend.models import IssueContributor

        assigned_ids = set(
            IssueContributor.objects.filter(
                user=request.user,
                role=IssueContributor.Role.COORDINATOR,
                status=IssueContributor.Status.ACTIVE,
            ).values_list("issue_id", flat=True)
        )
        issues = [i for i in issues if i.pk in assigned_ids]

    planka_url = getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "")
    issue_id = request.session.get("selected_issue_id")

    url_name = getattr(getattr(request, "resolver_match", None), "url_name", None)
    result = {
        "issues_for_sidebar": issues,
        "is_htmx": request.headers.get("HX-Request") == "true",
        "is_issue_page": url_name in ISSUE_PAGE_URL_NAMES,
        "is_coordinator_only": is_coordinator_only,
        "planka_url": planka_url,
    }

    if issue_id:
        issue = next((i for i in issues if i.pk == issue_id), None)
        if issue:
            result["session_selected_issue"] = issue
        else:
            request.session.pop("selected_issue_id", None)

    return result
