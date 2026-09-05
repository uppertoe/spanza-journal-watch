"""Curated collections, tags and MeSH mappings."""

import datetime
import logging
from collections import Counter, defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from spanza_journal_watch.submissions.models import (
    CuratedCollection,
    MeshTagMapping,
    Review,
    Tag,
)

from ..models import (
    PubmedArticle,
)

logger = logging.getLogger(__name__)


# ── Tag & MeSH mapping management ────────────────────────────────────────


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def collection_management(request):
    collections = (
        CuratedCollection.objects.all().prefetch_related("reviews", "tags").order_by("display_order", "-created")
    )
    return render(request, "backend/collection_management.html", {"collections": collections})


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def collection_create(request):
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        description = (request.POST.get("description") or "").strip()
        if not title:
            messages.error(request, "Title is required.")
            return redirect("backend:collection_management")
        collection = CuratedCollection.objects.create(title=title, description=description)
        messages.success(request, f"Collection '{collection.title}' created.")
        return redirect("backend:collection_edit", collection_id=collection.pk)
    return redirect("backend:collection_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def collection_edit(request, collection_id):
    collection = get_object_or_404(CuratedCollection, pk=collection_id)
    if request.method == "POST":
        collection.title = (request.POST.get("title") or "").strip() or collection.title
        collection.description = (request.POST.get("description") or "").strip()
        collection.active = request.POST.get("active") == "on"
        collection.save()
        messages.success(request, f"Collection '{collection.title}' updated.")
        return redirect("backend:collection_edit", collection_id=collection.pk)

    collection_reviews = list(
        collection.reviews.filter(active=True).select_related("author", "article__journal").order_by("-publish_date")
    )
    # All active reviews for the search/add interface
    all_reviews = (
        Review.objects.filter(active=True)
        .exclude(pk__in=[r.pk for r in collection_reviews])
        .select_related("author", "article__journal")
        .order_by("-publish_date")
    )
    query = (request.GET.get("q") or "").strip()
    if query:
        all_reviews = all_reviews.filter(
            Q(article__title__icontains=query)
            | Q(author__name__icontains=query)
            | Q(article__journal__name__icontains=query)
        )
    all_reviews = all_reviews[:50]

    return render(
        request,
        "backend/collection_edit.html",
        {
            "collection": collection,
            "collection_reviews": collection_reviews,
            "all_reviews": all_reviews,
            "query": query,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def collection_add_review(request, collection_id):
    collection = get_object_or_404(CuratedCollection, pk=collection_id)
    review_id = request.POST.get("review_id")
    if review_id:
        review = get_object_or_404(Review, pk=review_id, active=True)
        collection.reviews.add(review)
        messages.success(request, f"Added '{review.article.title[:50]}...' to collection.")
    return redirect("backend:collection_edit", collection_id=collection.pk)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def collection_remove_review(request, collection_id, review_id):
    collection = get_object_or_404(CuratedCollection, pk=collection_id)
    collection.reviews.remove(review_id)
    messages.success(request, "Review removed from collection.")
    return redirect("backend:collection_edit", collection_id=collection.pk)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def collection_delete(request, collection_id):
    collection = get_object_or_404(CuratedCollection, pk=collection_id)
    title = collection.title
    collection.delete()
    messages.success(request, f"Collection '{title}' deleted.")
    return redirect("backend:collection_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def tag_management(request):
    curated_tags = (
        Tag.objects.filter(curated=True)
        .annotate(
            article_count=Count("articles"),
            mapping_count=Count("mesh_mappings"),
        )
        .order_by("display_order", "text")
    )
    legacy_tags = (
        Tag.objects.filter(curated=False)
        .annotate(article_count=Count("articles"))
        .order_by("-article_count", "text")[:20]
    )
    return render(
        request,
        "backend/tag_management.html",
        {"curated_tags": curated_tags, "legacy_tags": legacy_tags},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def tag_toggle_curated(request, tag_id):
    tag = get_object_or_404(Tag, pk=tag_id)
    tag.curated = not tag.curated
    tag.active = True
    tag.save()
    messages.success(request, f"Tag #{tag.text} {'promoted to curated' if tag.curated else 'demoted from curated'}.")
    return redirect("backend:tag_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def tag_add(request):
    text = (request.POST.get("text") or "").strip().lower()
    text = slugify(text)
    if not text:
        messages.error(request, "Tag text is required.")
        return redirect("backend:tag_management")
    tag, created = Tag.objects.get_or_create(text=text, defaults={"curated": True, "active": True})
    if not created and not tag.curated:
        tag.curated = True
        tag.active = True
        tag.save()
        messages.success(request, f"Existing tag #{text} promoted to curated.")
    elif created:
        messages.success(request, f"Curated tag #{text} created.")
    else:
        messages.info(request, f"Tag #{text} already exists as curated.")
    return redirect("backend:tag_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def mesh_mapping_management(request):
    mappings = MeshTagMapping.objects.select_related("tag").order_by("tag__display_order", "tag__text", "mesh_term")

    # Group by tag
    grouped = defaultdict(list)
    for m in mappings:
        grouped[m.tag].append(m)

    curated_tags = Tag.objects.filter(curated=True).order_by("display_order", "text")
    return render(
        request,
        "backend/mesh_mapping_management.html",
        {"grouped_mappings": dict(grouped), "curated_tags": curated_tags, "total_mappings": mappings.count()},
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def mesh_mapping_add(request):
    mesh_term = (request.POST.get("mesh_term") or "").strip()
    tag_id = request.POST.get("tag_id")
    if not mesh_term or not tag_id:
        messages.error(request, "Both MeSH term and tag are required.")
        return redirect("backend:mesh_mapping_management")

    tag = get_object_or_404(Tag, pk=tag_id, curated=True)
    _, created = MeshTagMapping.objects.get_or_create(mesh_term=mesh_term, defaults={"tag": tag})
    if created:
        messages.success(request, f"Mapping added: {mesh_term} → #{tag.text}")
    else:
        messages.info(request, f"Mapping for '{mesh_term}' already exists.")
    return redirect("backend:mesh_mapping_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def mesh_mapping_delete(request, mapping_id):
    mapping = get_object_or_404(MeshTagMapping, pk=mapping_id)
    term = mapping.mesh_term
    mapping.delete()
    messages.success(request, f"Mapping for '{term}' deleted.")
    return redirect("backend:mesh_mapping_management")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def unmapped_mesh_report(request):
    """Show MeSH terms appearing on reviewed articles that have no mapping."""
    mapped_terms = set(MeshTagMapping.objects.values_list("mesh_term", flat=True))

    # Demographic/study design terms that are intentionally unmapped
    skip_terms = {
        "Humans",
        "Female",
        "Male",
        "Middle Aged",
        "Child",
        "Aged",
        "Adult",
        "Child, Preschool",
        "Infant",
        "Adolescent",
        "Young Adult",
        "Aged, 80 and over",
        "Infant, Newborn",
        "Animals",
        "Mice",
        "Rats",
        "Mice, Inbred C57BL",
        "Rats, Sprague-Dawley",
        "Prospective Studies",
        "Retrospective Studies",
        "Cohort Studies",
        "Double-Blind Method",
        "Randomized Controlled Trials as Topic",
        "Cross-Sectional Studies",
        "Follow-Up Studies",
        "Treatment Outcome",
        "Risk Factors",
        "Time Factors",
        "Incidence",
        "Prevalence",
        "Cells, Cultured",
        "Cell Line, Tumor",
        "Single-Blind Method",
        "Pilot Projects",
        "Feasibility Studies",
        "Qualitative Research",
        "Longitudinal Studies",
        "Case-Control Studies",
    }

    unmapped_counter = Counter()
    reviewed_articles = PubmedArticle.objects.filter(active=True)
    for article in reviewed_articles.iterator():
        for term in (article.metadata_json or {}).get("mesh_terms", []):
            if term not in mapped_terms and term not in skip_terms:
                unmapped_counter[term] += 1

    # Also check recent pipeline articles (last 3 months)
    pipeline_counter = Counter()
    three_months_ago = timezone.now().date() - datetime.timedelta(days=90)
    pipeline_articles = PubmedArticle.objects.filter(
        active=False,
        publication_date__gte=three_months_ago,
        pmid__isnull=False,
    )
    for article in pipeline_articles.iterator():
        for term in (article.metadata_json or {}).get("mesh_terms", []):
            if term not in mapped_terms and term not in skip_terms:
                pipeline_counter[term] += 1

    curated_tags = Tag.objects.filter(curated=True).order_by("display_order", "text")

    return render(
        request,
        "backend/unmapped_mesh_report.html",
        {
            "unmapped_reviewed": unmapped_counter.most_common(50),
            "unmapped_pipeline": pipeline_counter.most_common(50),
            "curated_tags": curated_tags,
        },
    )
