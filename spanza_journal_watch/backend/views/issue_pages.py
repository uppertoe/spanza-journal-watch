"""Issue set-up, review editing entry points, publishing and homepage selection."""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from spanza_journal_watch.submissions.models import (
    Issue,
    MeshTagMapping,
    Review,
)
from spanza_journal_watch.submissions.tasks import queue_indexnow_submission
from spanza_journal_watch.utils.cache import bump_content_cache_version

from ..forms import (
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
)
from ..models import (
    PubmedBatchArticle,
    PubmedImportBatch,
)
from ..planka import PlankaAPIError
from . import planka_boards
from .issue_context import _build_planka_publish_summary, _issue_builder_base_context
from .planka_boards import _build_planka_scope_counts, _filter_board_cards_by_scope
from .shared import (
    _check_coordinator_issue_access,
    _is_coordinator_only,
    _is_planka_board_not_found_error,
    _is_planka_connection_error,
    _resolve_and_persist_issue,
    _safe_planka_error,
)

logger = logging.getLogger(__name__)


def _build_article_mesh_context(article):
    """Return a list of dicts for each MeSH term on the article, with mapping status."""
    mesh_terms = (article.metadata_json or {}).get("mesh_terms", [])
    if not mesh_terms:
        return []
    mapped = dict(MeshTagMapping.objects.filter(mesh_term__in=mesh_terms).values_list("mesh_term", "tag__text"))
    return [{"term": t, "tag": mapped.get(t)} for t in sorted(mesh_terms)]


def _render_issue_panel(request, issue, review_form=None, form_action=None, is_edit=False):
    context = _issue_builder_base_context(
        issue=issue,
        review_form=review_form,
        form_action=form_action,
        is_edit=is_edit,
    )
    return render(request, "backend/issue_builder/_issue_reviews_panel.html", context)


def _get_issue_review_readiness(issue):
    if not issue:
        return []
    reviews = issue.reviews.select_related("author", "article").all()
    result = []
    for review in reviews:
        indicators = [
            {"label": "Body", "ok": bool((review.body or "").strip()), "required": True},
            {"label": "Author", "ok": review.author is not None, "required": True},
            {"label": "Article title", "ok": bool(review.article.get_title()), "required": True},
        ]
        if review.is_featured:
            indicators.append({"label": "Feature image", "ok": bool(review.feature_image), "required": True})
        is_ready = all(i["ok"] for i in indicators if i["required"])
        result.append({"review": review, "indicators": indicators, "is_ready": is_ready})
    return result


def _publish_summary(issue, readiness):
    """Counts and blockers shown at the top of the Publish tab."""
    if not issue:
        return None
    return {
        "total": len(readiness),
        "ready": sum(1 for item in readiness if item["is_ready"]),
        "live": sum(1 for item in readiness if item["review"].active),
        "errors": _validate_issue_publish(issue),
    }


def _validate_issue_publish(issue):
    errors = []
    max_featured = int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2))
    reviews = issue.reviews.select_related("article", "author").all()

    if not issue.name or not issue.body:
        errors.append("Issue requires a title and body before publishing.")

    if not reviews.exists():
        errors.append("Add at least one review before publishing.")

    featured_count = reviews.filter(is_featured=True).count()
    if featured_count > max_featured:
        errors.append(f"Only {max_featured} featured reviews are allowed.")

    for review in reviews:
        if not review.article_id:
            errors.append(f"Review {review.pk} is missing an article.")
        if not review.author_id:
            errors.append(f"Review {review.pk} is missing an author.")
        if not review.body:
            errors.append(f"Review {review.pk} is missing body content.")

    return errors


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_builder(request):
    if _is_coordinator_only(request.user):
        # Coordinators do not access the setup step; redirect to the reviewers page.
        issue_id = (request.GET.get("issue") or request.POST.get("issue") or "").strip()
        target = reverse("backend:issue_reviewers")
        if issue_id.isdigit():
            target += f"?issue={issue_id}"
        return redirect(target)

    issue = _resolve_and_persist_issue(request)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_reviewers(request):
    issue = _resolve_and_persist_issue(request)
    _check_coordinator_issue_access(request, issue)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_reviewers.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_reviews_edit(request):
    issue = _resolve_and_persist_issue(request)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_reviews_edit.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_publish(request):
    from spanza_journal_watch.layout.models import Homepage

    issue = _resolve_and_persist_issue(request)
    current_homepage = Homepage.get_current_homepage()
    context = _issue_builder_base_context(issue=issue)
    context["current_homepage"] = current_homepage
    context["review_readiness"] = _get_issue_review_readiness(issue)
    context["publish_summary"] = _publish_summary(issue, context["review_readiness"])
    return render(request, "backend/issue_builder/issue_publish.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_set_homepage(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    from spanza_journal_watch.layout.models import Homepage

    issue = _resolve_and_persist_issue(request)
    if not issue:
        messages.error(request, "No issue selected.")
        return redirect(reverse("backend:issue_publish"))
    Homepage.objects.update(publication_ready=False)
    homepage, _ = Homepage.objects.get_or_create(issue=issue)
    homepage.publication_ready = True
    homepage.save()
    Homepage.publish_homepage(homepage)
    messages.success(request, f'"{issue.name}" is now set as the homepage.')
    return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def toggle_review_active(request, review_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    from spanza_journal_watch.layout.models import Homepage

    review = get_object_or_404(Review, pk=review_id)
    issue = review.issues.first()

    review.active = not review.active
    review.save(update_fields=["active"])

    if review.active:
        if not review.article.active:
            review.article.active = True
            review.article.save(update_fields=["active", "modified"])
        if issue and not issue.active:
            issue.active = True
            issue.save(update_fields=["active", "modified"])
    else:
        if issue:
            any_active = issue.reviews.filter(active=True).exists()
            if not any_active:
                issue.active = False
                issue.save(update_fields=["active", "modified"])

    current_homepage = Homepage.get_current_homepage()
    context = _issue_builder_base_context(issue=issue)
    context["current_homepage"] = current_homepage
    context["review_readiness"] = _get_issue_review_readiness(issue)
    context["publish_summary"] = _publish_summary(issue, context["review_readiness"])
    return render(request, "backend/issue_builder/_publish_reviews_panel.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_planka_import(request):
    issue = _resolve_and_persist_issue(request)

    context = _issue_builder_base_context(issue=issue)
    binding = context.get("planka_binding")
    if issue and binding:
        card_scope = (request.GET.get("scope") or "publish").strip().lower()
        if card_scope not in {"publish", "all"}:
            card_scope = "publish"
        try:
            board_cards = planka_boards._extract_board_cards(binding)
            scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
            context["planka_publish_cards"] = scoped_cards
            context["planka_scope_counts"] = _build_planka_scope_counts(board_cards)
            context["planka_card_scope"] = card_scope
            context["planka_publish_summary"] = _build_planka_publish_summary(scoped_cards)
            if request.GET.get("refresh") == "1":
                summary = context["planka_publish_summary"]
                context["planka_panel_status"] = (
                    f"Refresh complete. {summary['total']} cards loaded in this view "
                    f"({summary['valid']} ready, {summary['missing']} with missing fields, "
                    f"{summary['already_imported']} already imported/protected)."
                )
                context["planka_panel_status_level"] = "success"
        except PlankaAPIError as error:
            safe_error = _safe_planka_error(error)
            context["planka_publish_cards"] = []
            context["planka_publish_summary"] = _build_planka_publish_summary([])
            context["planka_scope_counts"] = {"publish": 0, "all": 0}
            context["planka_card_scope"] = card_scope
            if _is_planka_connection_error(error):
                context["planka_panel_status"] = "Not connected to Planka. Retrying in background…"
                context["planka_disconnected"] = True
            elif _is_planka_board_not_found_error(error):
                context["planka_panel_status"] = (
                    "Linked Reviews board was not found in Planka. You can recreate the board for this issue."
                )
                context["planka_board_missing"] = True
            else:
                context["planka_panel_status"] = f"Could not refresh Planka cards: {safe_error}"
            context["planka_panel_status_level"] = "danger"

    if issue:
        staged_total = PubmedBatchArticle.objects.filter(issue=issue, is_selected=True).count()
        latest_batch = PubmedImportBatch.objects.filter(issue=issue).order_by("-created", "-pk").first()
        context["intake_staged_total"] = staged_total
        context["intake_batch_id"] = latest_batch.pk if latest_batch else ""

    return render(request, "backend/issue_builder/planka_import.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def save_issue_draft(request, issue_id=None):
    # Creating a new issue requires chief_editor; updating an existing one requires
    # only manage_issue_builder (already enforced by the decorator above).
    if issue_id is None and not request.user.has_perm("submissions.chief_editor"):
        raise PermissionDenied
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None
    _check_coordinator_issue_access(request, issue)
    form = IssueBuilderIssueForm(request.POST, request.FILES, instance=issue)

    if form.is_valid():
        issue = form.save(commit=False)
        if not issue.pk:
            issue.active = False
        issue.save()
        messages.success(request, "Issue draft saved.")
        return_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}"
        if request.headers.get("HX-Request") == "true":
            from django.http import HttpResponse as _HttpResponse

            response = _HttpResponse()
            response["HX-Redirect"] = return_url
            return response
        return redirect(return_url)

    context = _issue_builder_base_context(issue=issue)
    context["issue_form"] = form
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def new_review_form(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(issue=issue)
    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            "is_edit": False,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def publish_issue_bundle(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    errors = _validate_issue_publish(issue)

    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")

    with transaction.atomic():
        issue.active = True
        issue.save(update_fields=["active", "modified"])

        reviews = list(issue.reviews.select_related("article", "author").all())
        for review in reviews:
            if not review.article.active:
                review.article.active = True
                review.article.save(update_fields=["active", "modified"])
            if not review.active:
                review.active = True
                review.save()

        transaction.on_commit(bump_content_cache_version)

        # Ask Bing & co. to re-crawl everything this publish changed.
        indexnow_paths = ["/", reverse("submissions:issue_list"), issue.get_absolute_url()]
        indexnow_paths += [review.get_absolute_url() for review in reviews]
        indexnow_paths += [
            review.author.get_absolute_url() for review in reviews if review.author and not review.author.anonymous
        ]
        transaction.on_commit(lambda: queue_indexnow_submission(indexnow_paths))

    messages.success(request, "Issue, reviews, and articles are now live.")
    return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")
