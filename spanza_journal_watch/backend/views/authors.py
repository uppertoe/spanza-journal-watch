"""Author and affiliation (health service) management pages."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from spanza_journal_watch.submissions.models import (
    Author,
    HealthService,
)

from ..forms import (
    AuthorForm,
    HealthServiceForm,
)

logger = logging.getLogger(__name__)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def affiliations_list(request):
    if request.method == "POST":
        form = HealthServiceForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Affiliation added.")
            return redirect(reverse("backend:affiliations_list"))
    else:
        form = HealthServiceForm()

    affiliations = HealthService.objects.order_by("name")
    return render(
        request,
        "backend/affiliations.html",
        {
            "affiliations": affiliations,
            "form": form,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def affiliation_edit(request, affiliation_id):
    affiliation = get_object_or_404(HealthService, pk=affiliation_id)
    if request.method == "POST":
        form = HealthServiceForm(request.POST, request.FILES, instance=affiliation)
        if form.is_valid():
            form.save()
            messages.success(request, "Affiliation updated.")
            return redirect(reverse("backend:affiliations_list"))
    else:
        form = HealthServiceForm(instance=affiliation)

    affiliations = HealthService.objects.order_by("name")
    return render(
        request,
        "backend/affiliations.html",
        {
            "affiliations": affiliations,
            "form": form,
            "editing": affiliation,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def authors_list(request):
    q = request.GET.get("q", "").strip()
    if request.method == "POST":
        form = AuthorForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Author added.")
            return redirect(reverse("backend:authors_list"))
    else:
        form = AuthorForm()

    authors_qs = Author.objects.prefetch_related("health_services").order_by("name")
    if q:
        authors_qs = authors_qs.filter(Q(name__icontains=q) | Q(email__icontains=q))

    paginator = Paginator(authors_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "backend/authors.html",
        {
            "authors": page_obj,
            "form": form,
            "q": q,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def author_edit(request, author_id):
    author = get_object_or_404(Author, pk=author_id)
    if request.method == "POST":
        form = AuthorForm(request.POST, request.FILES, instance=author)
        if form.is_valid():
            form.save()
            messages.success(request, "Author updated.")
            return redirect(reverse("backend:authors_list"))
    else:
        form = AuthorForm(instance=author)

    authors_qs = Author.objects.prefetch_related("health_services").order_by("name")
    paginator = Paginator(authors_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "backend/authors.html",
        {
            "authors": page_obj,
            "form": form,
            "editing": author,
            "q": "",
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def author_merge(request, author_id):
    source = get_object_or_404(Author, pk=author_id)
    target = None
    preview = None

    target_id = request.POST.get("into") or request.GET.get("into")
    if target_id:
        target = get_object_or_404(Author, pk=target_id)
        if target.pk == source.pk:
            messages.warning(request, "Cannot merge an author into themselves.")
            return redirect(reverse("backend:author_merge", args=[source.pk]))

        review_count = source.reviews.count()
        membership_count = source.contributor_memberships.count()
        source_services = set(source.health_services.values_list("pk", flat=True))
        target_services = set(target.health_services.values_list("pk", flat=True))
        new_services = source_services - target_services

        preview = {
            "review_count": review_count,
            "membership_count": membership_count,
            "new_service_count": len(new_services),
            "new_services": list(source.health_services.filter(pk__in=new_services)),
            "email_action": "keep source's"
            if not target.email and source.email
            else ("no change" if target.email else "none"),
            "user_action": "keep source's"
            if not target.user and source.user
            else ("no change" if target.user else "none"),
            "image_action": "keep source's"
            if not target.profile_image and source.profile_image
            else ("no change" if target.profile_image else "none"),
        }

    if request.method == "POST" and target:
        with transaction.atomic():
            review_count = source.reviews.update(author=target)
            membership_count = source.contributor_memberships.update(author=target)
            target.health_services.add(*source.health_services.all())

            updated_fields = []
            if not target.email and source.email:
                target.email = source.email
                updated_fields.append("email")
            if not target.user and source.user:
                target.user = source.user
                updated_fields.append("user")
            if not target.profile_image and source.profile_image:
                target.profile_image = source.profile_image
                updated_fields.append("profile_image")
            if updated_fields:
                target.save(update_fields=updated_fields + ["modified"])

            source_name = f"{source.title} {source.name}"
            source.delete()

        messages.success(
            request,
            f"Merged {source_name} into {target.title} {target.name}: "
            f"{review_count} review(s), {membership_count} contributor membership(s) reassigned.",
        )
        return redirect(reverse("backend:authors_list"))

    all_authors = Author.objects.prefetch_related("health_services").exclude(pk=source.pk).order_by("name")
    return render(
        request,
        "backend/author_merge.html",
        {
            "source": source,
            "target": target,
            "preview": preview,
            "all_authors": all_authors,
        },
    )
