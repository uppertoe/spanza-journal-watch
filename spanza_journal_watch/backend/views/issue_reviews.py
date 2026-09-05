"""Adding, editing and removing reviews within an issue."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from spanza_journal_watch.submissions.models import (
    Author,
    Issue,
    Tag,
)

from ..forms import (
    IssueBuilderReviewForm,
)
from ..models import (
    PubmedArticle,
)
from ..pubmed import PubmedAPIError
from ..pubmed_cache import (
    build_pubmed_client as _build_pubmed_client,
)
from ..pubmed_cache import (
    upsert_pubmed_article as _upsert_pubmed_article,
)
from .issue_pages import _build_article_mesh_context, _render_issue_panel
from .shared import _queue_indexnow_for_review

logger = logging.getLogger(__name__)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def add_issue_review(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue)

    if form.is_valid():
        form.save()
        messages.success(request, "Review added to issue draft.")
        return _render_issue_panel(request, issue)

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


def _get_suggested_tag_pks(article, threshold=0.15, max_suggestions=6):
    """Return curated Tag PKs suggested by trigram similarity to article text."""
    search_text = f"{article.title or ''} {article.abstract or ''}".strip()
    if not search_text:
        return set()
    # Truncate to keep similarity computation reasonable
    search_text = search_text[:2000]
    suggested = (
        Tag.objects.filter(curated=True, active=True)
        .annotate(similarity=TrigramSimilarity("text", search_text))
        .filter(similarity__gte=threshold)
        .order_by("-similarity")
        .values_list("pk", flat=True)[:max_suggestions]
    )
    return set(suggested)


def _build_tag_grid_context(article):
    """Build annotated curated tag list for an article (MeSH-matched + similarity-suggested)."""
    curated_tags = list(Tag.objects.filter(curated=True, active=True).order_by("display_order"))
    mesh_tag_pks = set(article.tags.filter(curated=True).values_list("pk", flat=True))
    suggested_pks = _get_suggested_tag_pks(article) - mesh_tag_pks
    for tag in curated_tags:
        tag.mesh_matched = tag.pk in mesh_tag_pks
        tag.similarity_suggested = tag.pk in suggested_pks
    return curated_tags


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def review_pubmed_search(request, issue_id):
    """HTMX GET: search PubMed and return results for the review form."""
    get_object_or_404(Issue, pk=issue_id)
    query = (request.GET.get("q") or "").strip()
    articles = []
    error = None
    if query:
        try:
            articles = _build_pubmed_client().find_articles(query, retmax=8)
        except PubmedAPIError as exc:
            error = str(exc)
    # Mark articles that already exist locally
    if articles:
        existing_pmids = set(
            PubmedArticle.objects.filter(pmid__in=[a.get("pmid") for a in articles if a.get("pmid")]).values_list(
                "pmid", flat=True
            )
        )
        for a in articles:
            a["already_exists"] = a.get("pmid") in existing_pmids
    return render(
        request,
        "backend/issue_builder/_review_pubmed_search_results.html",
        {"articles": articles, "query": query, "error": error, "issue_id": issue_id},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def review_pubmed_select(request, issue_id):
    """HTMX POST: import/select a PubMed article and return citation card + tag grid OOB."""
    get_object_or_404(Issue, pk=issue_id)
    pmid = (request.POST.get("pmid") or "").strip()
    if not pmid:
        return HttpResponseBadRequest("No PMID provided.")

    try:
        payloads = _build_pubmed_client().fetch_articles([pmid])
    except PubmedAPIError as exc:
        return render(
            request,
            "backend/issue_builder/_review_pubmed_search_results.html",
            {"articles": [], "query": "", "error": str(exc), "issue_id": issue_id},
        )

    if not payloads:
        return render(
            request,
            "backend/issue_builder/_review_pubmed_search_results.html",
            {"articles": [], "query": "", "error": f"No article found for PMID {pmid}.", "issue_id": issue_id},
        )

    article = _upsert_pubmed_article(payloads[0])
    curated_tags = _build_tag_grid_context(article)
    mesh_context = _build_article_mesh_context(article)

    return render(
        request,
        "backend/issue_builder/_review_article_selected.html",
        {
            "article": article,
            "curated_tags": curated_tags,
            "article_mesh_terms": mesh_context,
            "issue_id": issue_id,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def review_tag_suggestions(request, article_id):
    """HTMX GET: return annotated tag grid for an article."""
    article = get_object_or_404(PubmedArticle, pk=article_id)
    curated_tags = _build_tag_grid_context(article)
    return render(
        request,
        "backend/issue_builder/_review_tag_grid_container.html",
        {"curated_tags": curated_tags},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def review_existing_article_search(request):
    """HTMX GET: return filtered PubmedArticle list for the existing-article picker."""
    query = (request.GET.get("q") or "").strip()
    if not query:
        return render(
            request, "backend/issue_builder/_review_existing_article_options.html", {"articles": [], "query": ""}
        )
    articles = list(PubmedArticle.objects.only("id", "title").filter(title__icontains=query).order_by("title")[:50])
    return render(
        request,
        "backend/issue_builder/_review_existing_article_options.html",
        {"articles": articles, "query": query},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def review_author_search(request):
    """HTMX GET: return filtered Author list for the author picker."""
    query = (request.GET.get("q") or "").strip()
    qs = Author.objects.prefetch_related("health_services").order_by("name")
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(health_services__name__icontains=query)).distinct()
    authors = list(qs[:50])
    return render(
        request,
        "backend/issue_builder/_review_author_options.html",
        {"authors": authors, "query": query},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def edit_issue_review_form(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(issue=issue, review=review)

    # Build MeSH term context for the article
    article = review.article
    mesh_context = _build_article_mesh_context(article)

    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse(
                "backend:update_issue_review",
                kwargs={"issue_id": issue.pk, "review_id": review.pk},
            ),
            "is_edit": True,
            "article_mesh_terms": mesh_context,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def update_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue, review=review)

    if form.is_valid():
        form.save()
        review.refresh_from_db()
        _queue_indexnow_for_review(review)
        messages.success(request, "Review updated.")
        return _render_issue_panel(request, issue)

    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse(
                "backend:update_issue_review", kwargs={"issue_id": issue.pk, "review_id": review.pk}
            ),
            "is_edit": True,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def remove_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    issue.reviews.remove(review)
    messages.success(request, "Review removed from issue.")

    return _render_issue_panel(request, issue)
