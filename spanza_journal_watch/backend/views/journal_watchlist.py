"""Watched journal list and editing."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import (
    WatchedJournalForm,
)
from ..models import (
    WatchedJournal,
)
from ..pubmed import PubmedAPIError
from ..pubmed_cache import (
    build_pubmed_client as _build_pubmed_client,
)
from .shared import _safe_planka_error

logger = logging.getLogger(__name__)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journals(request):
    form = WatchedJournalForm()

    if request.method == "POST":
        form = WatchedJournalForm(request.POST)
        if form.is_valid():
            watched = form.save()
            messages.success(request, f"Watched journal added: {watched.name}")
            return redirect(reverse("backend:watched_journals"))
        messages.error(request, "Could not save watched journal. Please check the form.")

    watched_items = WatchedJournal.objects.select_related("journal").order_by("name", "pk")
    context = {
        "watched_journal_form": form,
        "watched_journals": watched_items,
    }
    return render(request, "backend/watched_journals.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journal_search(request):
    if request.method != "GET":
        return HttpResponseBadRequest("Bad Request - GET only")

    query = (request.GET.get("q") or "").strip()
    if len(query) < 3:
        return JsonResponse({"results": []})

    try:
        journals = _build_pubmed_client().search_journals(query=query, retmax=20)
    except PubmedAPIError as error:
        return JsonResponse({"results": [], "error": _safe_planka_error(error)})

    return JsonResponse({"results": journals})


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journal_edit(request, watched_journal_id):
    watched = get_object_or_404(WatchedJournal, pk=watched_journal_id)
    form = WatchedJournalForm(instance=watched)

    if request.method == "POST":
        form = WatchedJournalForm(request.POST, instance=watched)
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated: {watched}")
            return redirect(reverse("backend:watched_journals"))
        messages.error(request, "Could not save changes. Please check the form.")

    return render(
        request,
        "backend/watched_journal_edit.html",
        {
            "watched_journal_form": form,
            "watched_journal": watched,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journal_delete(request, watched_journal_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")
    watched = get_object_or_404(WatchedJournal, pk=watched_journal_id)
    name = str(watched)
    watched.delete()
    messages.success(request, f"Deleted: {name}")
    return redirect(reverse("backend:watched_journals"))


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journal_toggle_active(request, watched_journal_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    watched = get_object_or_404(WatchedJournal, pk=watched_journal_id)
    watched.active = not watched.active
    watched.save(update_fields=["active", "modified"])
    messages.success(request, f"{watched.name}: {'active' if watched.active else 'inactive'}")
    return redirect(reverse("backend:watched_journals"))


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def watched_journal_toggle_frontend(request, watched_journal_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    watched = get_object_or_404(WatchedJournal, pk=watched_journal_id)
    watched.visible_on_frontend = not watched.visible_on_frontend
    watched.save(update_fields=["visible_on_frontend", "modified"])
    messages.success(request, f"{watched.name}: {'visible' if watched.visible_on_frontend else 'hidden'} on frontend")
    return redirect(reverse("backend:watched_journals"))
