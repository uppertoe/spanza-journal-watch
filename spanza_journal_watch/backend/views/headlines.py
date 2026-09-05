"""Editorial headline and bottom-line queue, with Claude drafting."""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from spanza_journal_watch.submissions.models import (
    Issue,
    Review,
)

from ..forms import (
    IssueBuilderReviewForm,
)
from ..models import (
    PubmedArticle,
)
from .shared import _queue_indexnow_for_review

logger = logging.getLogger(__name__)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def suggest_review_headline(request, issue_id):
    """Draft a headline and bottom line from the body text currently in the editor.

    Works on unsaved text: the form's body and article fields are posted, a
    throwaway Review is built from them, and the drafted values come back in
    the headline fields for the editor to accept or change.
    """
    from spanza_journal_watch.submissions.headlines import draft_review_headline

    issue = get_object_or_404(Issue, pk=issue_id)
    body = (request.POST.get("body") or "").strip()
    article = None
    existing_id = (request.POST.get("existing_article") or "").strip()
    if existing_id.isdigit():
        article = PubmedArticle.objects.filter(pk=existing_id).first()
    if article is None:
        article = PubmedArticle(title=(request.POST.get("article_name") or "").strip() or "Untitled article")
    review = Review(article=article, body=body)

    error = None
    draft = None
    if not body:
        error = "Paste or write the review body first, then ask for a suggestion."
    else:
        try:
            draft = draft_review_headline(review)
        except Exception:  # the SDK raises a family of errors; none should break the editor
            logger.exception("Headline suggestion failed for issue %s", issue.pk)
            error = "The suggestion service did not respond. Try again in a moment."
        if draft is None and error is None:
            error = "No suggestion came back. Check that ANTHROPIC_API_KEY is configured."

    form = IssueBuilderReviewForm(
        initial={"headline": draft["headline"] if draft else "", "bottom_line": draft["bottom_line"] if draft else ""},
        issue=issue,
    )
    return render(
        request,
        "backend/issue_builder/_review_headline_fields.html",
        {
            "review_form": form,
            "suggest_headline_url": reverse("backend:suggest_review_headline", kwargs={"issue_id": issue.pk}),
            "headline_suggested": bool(draft),
            "headline_suggest_error": error,
        },
    )


# ── Headlines queue (chief editor) ──────────────────────────────────────


def _headline_queue_queryset(status):
    # The row template reads the article's journal and, for reviews without a
    # publish date, the first issue; fetch both up front rather than per row.
    qs = (
        Review.objects.filter(active=True)
        .select_related("article", "article__journal", "author")
        .prefetch_related("issues")
        .order_by("-created")
    )
    if status == "missing":
        qs = qs.filter(editorial_headline="", draft_headline="")
    elif status == "draft":
        qs = qs.filter(editorial_headline="", draft_headline__gt="")
    elif status == "done":
        qs = qs.exclude(editorial_headline="")
    return qs


def _headline_row_context(review):
    from spanza_journal_watch.submissions.headlines import extract_bottom_line

    return {
        "review": review,
        "take_home": extract_bottom_line(review.body),
        "headline_value": review.editorial_headline or review.draft_headline,
        "bottom_line_value": review.bottom_line or review.draft_bottom_line,
        "anthropic_configured": bool(getattr(settings, "ANTHROPIC_API_KEY", "")),
    }


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def headline_queue(request):
    """Every live review with its headline state, for the chief editor to work through."""
    status = (request.GET.get("status") or "missing").strip()
    if status not in {"missing", "draft", "done", "all"}:
        status = "missing"
    reviews = list(_headline_queue_queryset(status)[:200])
    counts = {
        "missing": _headline_queue_queryset("missing").count(),
        "draft": _headline_queue_queryset("draft").count(),
        "done": _headline_queue_queryset("done").count(),
    }
    counts["all"] = Review.objects.filter(active=True).count()
    status_tabs = [
        ("missing", "Needs headline", counts["missing"]),
        ("draft", "Draft waiting", counts["draft"]),
        ("done", "Done", counts["done"]),
        ("all", "All", counts["all"]),
    ]
    context = {
        "status": status,
        "counts": counts,
        "status_tabs": status_tabs,
        "rows": [_headline_row_context(review) for review in reviews],
        "anthropic_configured": bool(getattr(settings, "ANTHROPIC_API_KEY", "")),
    }
    return render(request, "backend/headlines.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def headline_draft(request, review_id):
    """Draft one review now and return its refreshed row."""
    from spanza_journal_watch.submissions.tasks import draft_review_headline_task

    review = get_object_or_404(Review.objects.select_related("article", "author"), pk=review_id)
    error = None
    try:
        drafted = draft_review_headline_task(review.pk)
    except Exception:
        logger.exception("Headline draft failed for review %s", review.pk)
        drafted = False
        error = "The drafting service did not respond. Try again in a moment."
    if not drafted and error is None:
        error = "No draft came back. Check that ANTHROPIC_API_KEY is configured."
    review.refresh_from_db()
    context = _headline_row_context(review)
    context["row_error"] = error
    return render(request, "backend/_headline_row.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def headline_draft_all(request):
    """Queue drafting for every live review with neither a headline nor a draft."""
    from spanza_journal_watch.submissions.tasks import draft_missing_headlines_task

    if not getattr(settings, "ANTHROPIC_API_KEY", ""):
        messages.error(request, "ANTHROPIC_API_KEY is not configured, so nothing can be drafted.")
        return redirect("backend:headline_queue")
    pending = _headline_queue_queryset("missing").count()
    draft_missing_headlines_task.delay()
    messages.success(request, f"Drafting {pending} review(s) in the background. Reload in a few minutes.")
    return redirect(f"{reverse('backend:headline_queue')}?status=draft")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def headline_save(request, review_id):
    """Approve: copy the edited headline and bottom line onto the live review."""
    review = get_object_or_404(Review.objects.select_related("article", "author"), pk=review_id)
    headline = " ".join((request.POST.get("headline") or "").split())[:140]
    bottom_line = (request.POST.get("bottom_line") or "").strip()
    review.editorial_headline = headline
    review.bottom_line = bottom_line
    if headline:
        review.draft_headline = ""
        review.draft_bottom_line = ""
    review.save(update_fields=["editorial_headline", "bottom_line", "draft_headline", "draft_bottom_line", "modified"])
    _queue_indexnow_for_review(review)
    context = _headline_row_context(review)
    context["row_saved"] = True
    return render(request, "backend/_headline_row.html", context)
