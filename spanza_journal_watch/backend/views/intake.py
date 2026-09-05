"""Article intake: PubMed fetches, staging and pushing to Planka."""

import datetime
import logging
from collections import Counter

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from spanza_journal_watch.submissions.models import (
    Issue,
    Review,
)

from ..forms import (
    ArticleIntakeAssignIssueForm,
    ArticleIntakeFetchForm,
    PubmedApiKeyForm,
)
from ..models import (
    BackendPreference,
    PubmedArticle,
    PubmedArticleUserState,
    PubmedBatchArticle,
    PubmedBatchUserView,
    PubmedImportBatch,
    PubmedIntegrationCredential,
    WatchedJournal,
)
from ..planka import PlankaAPIError
from ..pubmed import PubmedAPIError
from ..pubmed_cache import (
    article_matches_metadata as _article_matches_metadata,
)
from ..pubmed_cache import (
    article_matches_topic as _article_matches_topic,
)
from ..pubmed_cache import (
    build_pubmed_client as _build_pubmed_client,
)
from ..pubmed_cache import (
    populate_pubmed_batch_from_cache,
)
from ..pubmed_cache import (
    shift_month as _shift_month,
)
from ..pubmed_cache import (
    upsert_pubmed_article as _upsert_pubmed_article,
)
from ..tasks import (
    check_batch_for_new_articles_task,
    run_pubmed_batch_push_task,
)
from . import planka_cards, shared
from .planka_cards import _attach_journal_label_to_card, _build_pubmed_planka_card, _get_issue_planka_candidates_list
from .shared import (
    _bool_from_value,
    _check_coordinator_issue_access,
    _get_planka_integration_credential,
    _is_planka_card_archived,
    _is_planka_card_not_found_error,
    _is_planka_list_not_found_error,
    _resolve_and_persist_issue,
    _safe_planka_error,
)
from .site_settings import (
    CARDIAC_MESH_TERMS,
    CARDIAC_TEXT_TERMS,
    HUMANS_MESH_TERM,
    ICU_MESH_TERMS,
    ICU_TEXT_TERMS,
    NEONATAL_MESH_TERMS,
    NEONATAL_TEXT_TERMS,
    PAEDIATRIC_MESH_TERMS,
    PAEDIATRIC_TEXT_TERMS,
    PAIN_MESH_TERMS,
    PAIN_TEXT_TERMS,
    REVIEW_PUBLICATION_TYPES,
    TRIAL_PUBLICATION_TYPES,
    _get_backend_preference,
    _get_pubmed_integration_credential,
)

logger = logging.getLogger(__name__)


def _param_enabled(params, key, default=False):
    raw = params.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


PUBMED_FETCH_FRESHNESS = datetime.timedelta(minutes=15)


def _get_or_create_user_view(batch, user):
    if not getattr(user, "is_authenticated", False):
        return None
    user_view, _ = PubmedBatchUserView.objects.get_or_create(batch=batch, user=user)
    return user_view


def _pubmed_fetch_gate_remaining(batch):
    """Return timedelta until next PubMed fetch is allowed, or None if allowed now."""
    last = batch.last_pubmed_fetched_at
    if last is None:
        return None
    elapsed = timezone.now() - last
    if elapsed >= PUBMED_FETCH_FRESHNESS:
        return None
    return PUBMED_FETCH_FRESHNESS - elapsed


INTAKE_RECHECK_SETTLE_DAYS = 14
INTAKE_RECHECK_DUE_DAYS = 7


def _intake_recheck_state(batch, *, today=None, now=None):
    """Describe where the batch's window sits relative to today, for the re-check card.

    PubMed indexes an article some days after it appears online, so a list loaded
    while the window is still open is incomplete. Phases:
      open     - today is inside the window; check again at the end of each month
      closing  - the window closed less than INTAKE_RECHECK_SETTLE_DAYS ago; late
                 articles from the final month are still arriving
      settled  - the window closed long enough ago that PubMed should hold everything
    `due` is set when the list has never been checked, or the last check is older
    than INTAKE_RECHECK_DUE_DAYS while the window is not yet settled.
    """
    now = now or timezone.now()
    today = today or timezone.localdate(now)
    window_end = _shift_month(batch.to_month, 1) - datetime.timedelta(days=1)
    settle_date = window_end + datetime.timedelta(days=INTAKE_RECHECK_SETTLE_DAYS)
    last = batch.last_pubmed_fetched_at
    days_since_check = (now - last).days if last else None

    if today <= window_end:
        phase = "open"
        month_end = _shift_month(today.replace(day=1), 1) - datetime.timedelta(days=1)
        next_check = min(month_end, window_end)
    elif today <= settle_date:
        phase = "closing"
        next_check = settle_date
    else:
        phase = "settled"
        next_check = None

    due = last is None or (phase != "settled" and days_since_check >= INTAKE_RECHECK_DUE_DAYS)
    return {
        "phase": phase,
        "window_end": window_end,
        "settle_date": settle_date,
        "next_check": next_check,
        "last_checked_at": last,
        "days_since_check": days_since_check,
        "due": due,
    }


def _import_pubmed_batch(batch, watched_journals):
    populate_pubmed_batch_from_cache(batch, watched_journals)


def _build_article_intake_queryset(batch, params):
    """Return (rows, tab_rows, flags) where tab_rows ignores the journal filter.

    `rows` and `tab_rows` are either lists (when Python-side topic filters force
    materialization) or QuerySets (the fast path, letting Paginator use SQL COUNT).
    """
    query = (params.get("q") or "").strip()
    watched_journal_id = (params.get("journal") or "").strip()
    selected = (params.get("filter_selected") or params.get("selected") or "").strip().lower()
    # Paediatric-only is on by default: the results form carries an explicit 0/1
    # so a coordinator can still switch it off.
    paediatric_only = _param_enabled(params, "paediatric_only", default=True)
    humans_only = _param_enabled(params, "humans_only", default=False)
    review_only = _param_enabled(params, "review_only", default=False)
    trial_only = _param_enabled(params, "trial_only", default=False)
    pain_only = _param_enabled(params, "pain_only", default=False)
    icu_only = _param_enabled(params, "icu_only", default=False)
    cardiac_only = _param_enabled(params, "cardiac_only", default=False)
    neonatal_only = _param_enabled(params, "neonatal_only", default=False)

    base = (
        batch.batch_articles.select_related("article", "watched_journal", "issue")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            ),
            visitor_recommendation_count=Count("article__visitor_recommendations", distinct=True),
        )
        .order_by("-article__publication_date", "article__title")
    )

    if query:
        base = base.filter(
            Q(article__title__icontains=query)
            | Q(article__abstract__icontains=query)
            | Q(article__doi__icontains=query)
            | Q(article__pmid__icontains=query)
        )
    if selected in {"true", "false"}:
        base = base.filter(is_selected=(selected == "true"))

    has_python_filter = any(
        [paediatric_only, humans_only, review_only, trial_only, pain_only, icu_only, cardiac_only, neonatal_only]
    )

    if has_python_filter:
        # Materialize once (without journal filter) so rows and tab_rows share a pass.
        materialized = list(base)

        def _apply_python_filters(rows_list):
            if paediatric_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_topic(
                        r.article, mesh_terms=PAEDIATRIC_MESH_TERMS, text_terms=PAEDIATRIC_TEXT_TERMS
                    )
                ]
            if humans_only:
                rows_list = [
                    r for r in rows_list if _article_matches_metadata(r.article, "mesh_terms", {HUMANS_MESH_TERM})
                ]
            if review_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_metadata(r.article, "publication_types", REVIEW_PUBLICATION_TYPES)
                ]
            if trial_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_metadata(r.article, "publication_types", TRIAL_PUBLICATION_TYPES)
                ]
            if pain_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_topic(r.article, mesh_terms=PAIN_MESH_TERMS, text_terms=PAIN_TEXT_TERMS)
                ]
            if icu_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_topic(r.article, mesh_terms=ICU_MESH_TERMS, text_terms=ICU_TEXT_TERMS)
                ]
            if cardiac_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_topic(r.article, mesh_terms=CARDIAC_MESH_TERMS, text_terms=CARDIAC_TEXT_TERMS)
                ]
            if neonatal_only:
                rows_list = [
                    r
                    for r in rows_list
                    if _article_matches_topic(
                        r.article, mesh_terms=NEONATAL_MESH_TERMS, text_terms=NEONATAL_TEXT_TERMS
                    )
                ]
            return rows_list

        tab_rows = _apply_python_filters(materialized)
        if watched_journal_id.isdigit():
            wj = int(watched_journal_id)
            rows = [r for r in tab_rows if r.watched_journal_id == wj]
        else:
            rows = tab_rows
    else:
        tab_rows = base
        if watched_journal_id.isdigit():
            rows = base.filter(watched_journal_id=int(watched_journal_id))
        else:
            rows = base

    flags = {
        "query": query,
        "watched_journal_id": watched_journal_id,
        "selected": selected,
        "paediatric_only": paediatric_only,
        "humans_only": humans_only,
        "review_only": review_only,
        "trial_only": trial_only,
        "pain_only": pain_only,
        "icu_only": icu_only,
        "cardiac_only": cardiac_only,
        "neonatal_only": neonatal_only,
    }
    return rows, tab_rows, flags


def _article_intake_results_context(batch, params, user=None):
    watched_options = list(batch.watched_journals.order_by("name"))

    rows, tab_rows, flags = _build_article_intake_queryset(batch, params)
    new_only = _param_enabled(params, "new_only", default=False)

    user_view = _get_or_create_user_view(batch, user) if user is not None else None
    seen_baseline = user_view.last_seen_at if user_view else None
    seen_ids = set(user_view.seen_batch_article_ids or []) if user_view else set()

    def _row_is_new(row):
        if seen_baseline is None:
            return False
        return row.created > seen_baseline and row.pk not in seen_ids

    if isinstance(tab_rows, list):
        journal_counts = Counter(r.watched_journal_id for r in tab_rows)
        all_journals_count = len(tab_rows)
    else:
        journal_counts = dict(
            tab_rows.order_by()
            .values("watched_journal_id")
            .annotate(c=Count("id"))
            .values_list("watched_journal_id", "c")
        )
        all_journals_count = tab_rows.count()

    new_count = 0
    if seen_baseline is not None:
        if isinstance(tab_rows, list):
            new_count = sum(1 for r in tab_rows if _row_is_new(r))
        else:
            new_count = sum(
                1
                for pk, created in tab_rows.values_list("id", "created").iterator()
                if created > seen_baseline and pk not in seen_ids
            )

    if new_only and seen_baseline is not None:
        if isinstance(rows, list):
            rows = [r for r in rows if _row_is_new(r)]
        else:
            rows = list(rows)
            rows = [r for r in rows if _row_is_new(r)]

    watched_journal_tabs = [
        {"journal": watched, "count": journal_counts.get(watched.pk, 0)} for watched in watched_options
    ]

    paginator = Paginator(rows, 25)
    page_obj = paginator.get_page(params.get("page") or 1)
    visible_rows = list(page_obj.object_list)
    for row in visible_rows:
        row.is_new = _row_is_new(row)
    all_visible_selected = bool(visible_rows) and all(row.is_selected for row in visible_rows)
    result_total = len(rows) if isinstance(rows, list) else rows.count()
    staged_rows = list(
        batch.batch_articles.select_related("article", "watched_journal", "issue")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            ),
            visitor_recommendation_count=Count("article__visitor_recommendations", distinct=True),
        )
        .filter(is_selected=True)
        .order_by("-modified")[:200]
    )
    return {
        "batch": batch,
        "page_obj": page_obj,
        "result_rows": visible_rows,
        "all_visible_selected": all_visible_selected,
        "all_journals_count": all_journals_count,
        "staged_rows": staged_rows,
        "result_total": result_total,
        "selected_total": batch.batch_articles.filter(is_selected=True).count(),
        "pushed_total": batch.batch_articles.exclude(planka_card_id="").count(),
        "filter_query": flags["query"],
        "filter_journal": flags["watched_journal_id"],
        "filter_selected": flags["selected"],
        "filter_paediatric_only": flags["paediatric_only"],
        "filter_humans_only": flags["humans_only"],
        "filter_review_only": flags["review_only"],
        "filter_trial_only": flags["trial_only"],
        "filter_pain_only": flags["pain_only"],
        "filter_icu_only": flags["icu_only"],
        "filter_cardiac_only": flags["cardiac_only"],
        "filter_neonatal_only": flags["neonatal_only"],
        "watched_journal_options": watched_options,
        "watched_journal_tabs": watched_journal_tabs,
        "new_count": new_count,
        "filter_new_only": new_only,
        "user_view": user_view,
        "last_pubmed_fetched_at": batch.last_pubmed_fetched_at,
        "recheck": _intake_recheck_state(batch),
        "fetch_gate_remaining_seconds": (
            int(_pubmed_fetch_gate_remaining(batch).total_seconds())
            if _pubmed_fetch_gate_remaining(batch) is not None
            else 0
        ),
    }


def _enrich_find_articles(articles, batch_article_map):
    """Merge PubMed search results with batch staging state."""
    enriched = []
    for art in articles:
        pmid = art.get("pmid", "")
        info = batch_article_map.get(pmid)
        enriched.append(
            {
                **art,
                "in_batch": info is not None,
                "is_selected": info["is_selected"] if info else False,
            }
        )
    return enriched


def _render_article_intake_results_response(request, batch, params, *, message_target="global"):
    context = _article_intake_results_context(batch, params, user=request.user)
    context["batch_task_running"] = batch.task_state in {
        PubmedImportBatch.TASK_STATE_PENDING,
        PubmedImportBatch.TASK_STATE_RUNNING,
    }
    context["batch_task_done"] = batch.task_state in {
        PubmedImportBatch.TASK_STATE_SUCCESS,
        PubmedImportBatch.TASK_STATE_ERROR,
    }
    if request.headers.get("HX-Request") == "true":
        if message_target == "push":
            template = "backend/_article_intake_results_with_push_messages.html"
        else:
            template = "backend/_article_intake_results_with_messages.html"
    else:
        template = "backend/_article_intake_results.html"
    return render(request, template, context)


def _queue_batch_task(batch, *, action, note, task_callable, task_args=None):
    task_args = task_args or []
    batch.task_action = action
    batch.task_state = PubmedImportBatch.TASK_STATE_PENDING
    batch.task_note = note
    batch.task_id = ""
    batch.save(update_fields=["task_action", "task_state", "task_note", "task_id", "modified"])

    async_result = task_callable.delay(*task_args)
    batch.task_id = async_result.id or ""
    batch.task_state = PubmedImportBatch.TASK_STATE_RUNNING
    batch.save(update_fields=["task_id", "task_state", "modified"])
    return async_result


def _compose_month_value(year_value, month_value):
    try:
        year = int(str(year_value).strip())
        month = int(str(month_value).strip())
    except (TypeError, ValueError):
        return ""

    if year < 1900 or month < 1 or month > 12:
        return ""

    return f"{year:04d}-{month:02d}"


def _parse_month_parts(value, fallback_date):
    text = str(value or "").strip()
    if text:
        parts = text.split("-", 2)
        if len(parts) >= 2:
            try:
                year = int(parts[0])
                month = int(parts[1])
                if 1900 <= year and 1 <= month <= 12:
                    return year, month
            except (TypeError, ValueError):
                pass

    return fallback_date.year, fallback_date.month


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake(request):
    credential = _get_pubmed_integration_credential()
    current_month = timezone.now().date().replace(day=1)
    selected_issue = _resolve_and_persist_issue(request)
    _check_coordinator_issue_access(request, selected_issue)
    active_issue = Issue.objects.filter(active=True).order_by("-date", "-pk").first()
    issue_anchor_date = (selected_issue.date if selected_issue and selected_issue.date else current_month).replace(
        day=1
    )
    default_from_month = _shift_month(issue_anchor_date, -4)
    default_to_month = _shift_month(issue_anchor_date, -2)
    backend_preference = _get_backend_preference()
    active_watched_ids = list(WatchedJournal.objects.filter(active=True).values_list("pk", flat=True))
    default_watched_ids = []
    if backend_preference:
        default_watched_ids = list(
            backend_preference.default_watched_journals.filter(active=True).values_list("pk", flat=True)
        )
    if not default_watched_ids:
        default_watched_ids = active_watched_ids

    latest_issue_batch = None
    if selected_issue:
        latest_issue_batch = PubmedImportBatch.objects.filter(issue=selected_issue).order_by("-created", "-pk").first()
        if latest_issue_batch:
            default_from_month = latest_issue_batch.from_month
            default_to_month = latest_issue_batch.to_month
            latest_batch_watched_ids = list(
                latest_issue_batch.watched_journals.filter(active=True).values_list("pk", flat=True)
            )
            if latest_batch_watched_ids:
                default_watched_ids = latest_batch_watched_ids

    fetch_form = ArticleIntakeFetchForm(
        initial={
            "issue": selected_issue.pk if selected_issue else None,
            "from_month": default_from_month.strftime("%Y-%m"),
            "to_month": default_to_month.strftime("%Y-%m"),
            "watched_journals": default_watched_ids,
        },
    )
    assign_issue_form = ArticleIntakeAssignIssueForm()

    if request.method == "POST" and request.POST.get("action") == "fetch":
        if not selected_issue:
            messages.error(request, "Select an issue before fetching articles.")
            return redirect(reverse("backend:article_intake"))

        fetch_payload = request.POST.copy()
        fetch_payload["issue"] = str(selected_issue.pk)
        if not (fetch_payload.get("from_month") or "").strip():
            from_month_value = _compose_month_value(
                fetch_payload.get("from_month_year"),
                fetch_payload.get("from_month_month"),
            )
            if from_month_value:
                fetch_payload["from_month"] = from_month_value
        if not (fetch_payload.get("to_month") or "").strip():
            to_month_value = _compose_month_value(
                fetch_payload.get("to_month_year"),
                fetch_payload.get("to_month_month"),
            )
            if to_month_value:
                fetch_payload["to_month"] = to_month_value

        fetch_form = ArticleIntakeFetchForm(fetch_payload)
        if fetch_form.is_valid():
            batch = PubmedImportBatch.objects.create(
                issue=selected_issue,
                created_by=request.user,
                from_month=fetch_form.cleaned_data["from_month"],
                to_month=fetch_form.cleaned_data["to_month"],
                keyword_query="",
            )
            watched_journals = list(fetch_form.cleaned_data["watched_journals"])
            batch.watched_journals.set(watched_journals)

            preference = backend_preference or BackendPreference(singleton=1)
            preference.save()
            preference.default_watched_journals.set(watched_journals)

            try:
                _import_pubmed_batch(batch, watched_journals)
            except PubmedAPIError as error:
                messages.error(request, f"Could not build batch from cache: {_safe_planka_error(error)}")
                return redirect(f"{reverse('backend:article_intake')}?issue={selected_issue.pk}&batch={batch.pk}")

            # Kick off a PubMed check in the background so new articles flow in
            # without the user having to click anything for the first look.
            _queue_batch_task(
                batch,
                action="check_for_new",
                note="Queued PubMed check for new articles.",
                task_callable=check_batch_for_new_articles_task,
                task_args=[batch.pk],
            )
            messages.success(
                request,
                f"Loaded {batch.result_count} cached article(s). "
                "Checking PubMed for newer articles in the background.",
            )

            return redirect(f"{reverse('backend:article_intake')}?issue={selected_issue.pk}&batch={batch.pk}")

    batch_id = (request.GET.get("batch") or "").strip()
    batch = None
    if batch_id.isdigit():
        batch = PubmedImportBatch.objects.filter(pk=int(batch_id)).first()
    elif selected_issue:
        batch = PubmedImportBatch.objects.filter(issue=selected_issue).order_by("-created", "-pk").first()
    if not batch:
        batch = None

    if batch:
        assign_issue_form = ArticleIntakeAssignIssueForm(initial={"issue": batch.issue_id})

    from_month_value = fetch_form["from_month"].value() or current_month.strftime("%Y-%m")
    to_month_value = fetch_form["to_month"].value() or current_month.strftime("%Y-%m")
    from_month_year, from_month_month = _parse_month_parts(from_month_value, current_month)
    to_month_year, to_month_month = _parse_month_parts(to_month_value, current_month)
    year_start = min(current_month.year - 10, issue_anchor_date.year - 2)
    year_end = max(current_month.year + 2, issue_anchor_date.year + 2)
    year_options = list(range(year_start, year_end + 1))
    month_options = [(index, datetime.date(2000, index, 1).strftime("%B")) for index in range(1, 13)]

    context = {
        "pubmed_credential": credential,
        "planka_credential": _get_planka_integration_credential(),
        "fetch_form": fetch_form,
        "assign_issue_form": assign_issue_form,
        "batch": batch,
        "active_issue": active_issue,
        "selected_issue": selected_issue,
        "issue_options": Issue.objects.order_by("-date", "-pk")[:50],
        "from_month_year": from_month_year,
        "from_month_month": from_month_month,
        "to_month_year": to_month_year,
        "to_month_month": to_month_month,
        "year_options": year_options,
        "month_options": month_options,
    }
    if batch:
        issue, binding, list_error = _get_issue_planka_candidates_list(batch)
        context["planka_issue"] = issue
        context["planka_binding"] = binding
        context["planka_push_hint"] = list_error
        context["batch_task_running"] = batch.task_state in {
            PubmedImportBatch.TASK_STATE_PENDING,
            PubmedImportBatch.TASK_STATE_RUNNING,
        }
        context["batch_task_done"] = batch.task_state in {
            PubmedImportBatch.TASK_STATE_SUCCESS,
            PubmedImportBatch.TASK_STATE_ERROR,
        }
        context["show_stage2_task_status"] = context["batch_task_running"] and (batch.task_action != "push")
        context["show_push_task_status"] = context["batch_task_running"] and (batch.task_action == "push")
        context.update(_article_intake_results_context(batch, request.GET, user=request.user))

    return render(request, "backend/article_intake.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def pubmed_save_api_key(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    form = PubmedApiKeyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please provide a valid PubMed API key.")
        return redirect(reverse("backend:article_intake"))

    api_key = form.cleaned_data["api_key"]
    try:
        validator = _build_pubmed_client(api_key=api_key)
        validator.ping()
        credential = _get_pubmed_integration_credential() or PubmedIntegrationCredential(singleton=1)
        credential.set_api_key(api_key)
        credential.configured_by = request.user
        credential.last_validated_at = timezone.now()
        credential.last_error = ""
        credential.save()
        messages.success(request, "PubMed API key saved successfully.")
    except PubmedAPIError as error:
        safe_error = _safe_planka_error(error)
        credential = _get_pubmed_integration_credential() or PubmedIntegrationCredential(singleton=1)
        credential.last_error = safe_error
        credential.save()
        messages.error(request, f"Could not validate PubMed API key: {safe_error}")

    if request.POST.get("next") == "settings":
        return redirect(reverse("backend:backend_settings"))
    return redirect(reverse("backend:article_intake"))


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_results(request, batch_id):
    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    context = _article_intake_results_context(batch, request.GET, user=request.user)
    return render(request, "backend/_article_intake_results.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_find_article(request, batch_id):
    """HTMX GET: search PubMed by free text and return a results partial."""
    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    query = (request.GET.get("q") or "").strip()

    articles = []
    error = None

    if query:
        try:
            articles = _build_pubmed_client().find_articles(query, retmax=8)
        except PubmedAPIError as exc:
            error = _safe_planka_error(exc)

    batch_article_map = {
        ba.article.pmid: {"item_id": ba.pk, "is_selected": ba.is_selected}
        for ba in batch.batch_articles.select_related("article")
    }
    enriched = _enrich_find_articles(articles, batch_article_map)

    return render(
        request,
        "backend/_article_intake_find_article.html",
        {
            "batch": batch,
            "query": query,
            "articles": enriched,
            "error": error,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_add_article(request, batch_id):
    """POST: toggle staging of a specific article (add if new, stage/unstage if existing)."""

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    pmid = (request.POST.get("pmid") or "").strip()
    query = (request.POST.get("q") or "").strip()

    if not pmid:
        messages.error(request, "No PMID provided.")
        return _render_article_intake_results_response(request, batch, request.POST)

    # Check if article is already in the batch — if so, toggle staging without re-fetching
    existing_link = (
        PubmedBatchArticle.objects.filter(batch=batch, article__pmid=pmid).select_related("article").first()
    )

    if existing_link:
        article = existing_link.article
        new_selected = not existing_link.is_selected
        existing_link.is_selected = new_selected
        existing_link.save(update_fields=["is_selected", "modified"])
        if new_selected:
            messages.success(request, f"\u201c{article.title}\u201d added to staging.")
        else:
            messages.info(request, f"\u201c{article.title}\u201d removed from staging.")
    else:
        try:
            payloads = _build_pubmed_client().fetch_articles([pmid])
        except PubmedAPIError as exc:
            messages.error(request, f"PubMed lookup failed: {_safe_planka_error(exc)}")
            return _render_article_intake_results_response(request, batch, request.POST)

        if not payloads:
            messages.error(request, f"No article found for PMID {pmid}.")
            return _render_article_intake_results_response(request, batch, request.POST)

        article = _upsert_pubmed_article(payloads[0])
        if article is None:
            messages.error(request, f"No article found for PMID {pmid}.")
            return _render_article_intake_results_response(request, batch, request.POST)

        PubmedBatchArticle.objects.create(batch=batch, article=article, issue=batch.issue, is_selected=True)
        messages.success(request, f"\u201c{article.title}\u201d added to staging.")
        new_selected = True

    batch.result_count = batch.batch_articles.count()
    batch.selected_count = batch.batch_articles.filter(is_selected=True).count()
    batch.save(update_fields=["result_count", "selected_count", "modified"])

    highlighted_pmid = pmid if new_selected else None

    # Build results table HTML
    results_context = _article_intake_results_context(batch, request.POST, user=request.user)
    results_context["highlighted_pmid"] = highlighted_pmid
    results_context["batch_task_running"] = False
    results_context["batch_task_done"] = False
    results_html = render_to_string(
        "backend/_article_intake_results_with_messages.html", results_context, request=request
    )

    if not query or request.headers.get("HX-Request") != "true":
        from django.http import HttpResponse as _HttpResponse

        return _HttpResponse(results_html)

    # OOB: re-run the search so the find panel reflects the new staging state
    try:
        find_articles_raw = _build_pubmed_client().find_articles(query, retmax=8)
    except PubmedAPIError:
        find_articles_raw = []

    batch_article_map = {
        ba.article.pmid: {"item_id": ba.pk, "is_selected": ba.is_selected}
        for ba in batch.batch_articles.select_related("article")
    }
    find_html = render_to_string(
        "backend/_article_intake_find_article.html",
        {
            "batch": batch,
            "query": query,
            "articles": _enrich_find_articles(find_articles_raw, batch_article_map),
            "error": None,
        },
        request=request,
    )
    oob_html = f'<div id="find-article-results" hx-swap-oob="innerHTML">{find_html}</div>'

    from django.http import HttpResponse as _HttpResponse

    return _HttpResponse(results_html + oob_html)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_toggle_selection(request, batch_id, item_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    item = get_object_or_404(PubmedBatchArticle, pk=item_id, batch=batch)
    item.is_selected = _bool_from_value(request.POST.get("selected"))
    item.save(update_fields=["is_selected", "modified"])

    batch.selected_count = batch.batch_articles.filter(is_selected=True).count()
    batch.save(update_fields=["selected_count", "modified"])

    # Engaging with a row implicitly acknowledges its "new" indicator.
    user_view = _get_or_create_user_view(batch, request.user)
    if user_view is not None:
        seen = list(user_view.seen_batch_article_ids or [])
        if item.pk not in seen:
            seen.append(item.pk)
            user_view.seen_batch_article_ids = seen
            user_view.save(update_fields=["seen_batch_article_ids", "modified"])

    return _render_article_intake_results_response(request, batch, request.POST)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_bulk_selection(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    action = (request.POST.get("bulk_action") or "").strip().lower()
    rows, *_ = _build_article_intake_queryset(batch, request.POST)
    row_ids = [item.pk for item in rows]
    if action == "select_all":
        PubmedBatchArticle.objects.filter(pk__in=row_ids).update(is_selected=True)
    elif action == "select_none":
        PubmedBatchArticle.objects.filter(pk__in=row_ids).update(is_selected=False)
    elif action in {"stage_checked", "unstage_checked"}:
        raw_checked_values = list(request.POST.getlist("row_ids"))
        persisted_checked = (request.POST.get("persisted_row_ids") or "").strip()
        if persisted_checked:
            raw_checked_values.extend(persisted_checked.split(","))

        checked_ids = []
        for item in raw_checked_values:
            for token in str(item).split(","):
                value = token.strip()
                if value.isdigit():
                    checked_ids.append(int(value))

        checked_ids = list(set(checked_ids))
        target_rows = list(PubmedBatchArticle.objects.filter(batch=batch, pk__in=checked_ids))

        if action == "stage_checked":
            PubmedBatchArticle.objects.filter(pk__in=[item.pk for item in target_rows]).update(is_selected=True)
        else:
            remove_from_planka = _bool_from_value(request.POST.get("remove_from_planka"))
            removed_count = 0
            skipped_count = 0
            failed_count = 0
            missing_count = 0
            kept_staged_count = 0

            planka_client = None
            candidates_list_id = ""
            list_type_map = {}
            if remove_from_planka:
                issue, binding, list_error = _get_issue_planka_candidates_list(batch)
                if list_error:
                    messages.warning(request, f"Could not remove cards from Planka: {list_error}")
                else:
                    try:
                        planka_client = shared._build_planka_client()
                        candidates_list_id = binding.get_list_id("candidates")
                        list_type_map = planka_cards._get_board_list_type_map(
                            client=planka_client, board_id=binding.board_id
                        )
                    except PlankaAPIError as error:
                        messages.warning(request, f"Could not connect to Planka: {_safe_planka_error(error)}")

            for row in target_rows:
                should_unstage = True
                if remove_from_planka and row.planka_card_id and planka_client and candidates_list_id:
                    try:
                        card = planka_client.get_card(row.planka_card_id)
                        if _is_planka_card_archived(card):
                            row.planka_card_id = ""
                            row.planka_card_url = ""
                            row.planka_pushed_at = None
                            row.planka_push_error = (
                                "Planka status: card deleted/archived in Planka. "
                                "It will be recreated on next push while staged."
                            )
                            missing_count += 1
                            should_unstage = False
                        else:
                            card_list_id = str(card.get("listId") or "")
                            card_list_type = list_type_map.get(card_list_id, "")
                            if card_list_type == "trash":
                                row.planka_card_id = ""
                                row.planka_card_url = ""
                                row.planka_pushed_at = None
                                row.planka_push_error = (
                                    "Planka status: card deleted/archived in Planka. "
                                    "It will be recreated on next push while staged."
                                )
                                missing_count += 1
                                should_unstage = False
                            elif card_list_id == str(candidates_list_id):
                                planka_client.delete_card(row.planka_card_id)
                                row.planka_card_id = ""
                                row.planka_card_url = ""
                                row.planka_pushed_at = None
                                row.planka_push_error = ""
                                removed_count += 1
                            else:
                                row.planka_push_error = (
                                    "Planka status: card moved from Candidates; still staged for traceability."
                                )
                                skipped_count += 1
                                should_unstage = False
                    except PlankaAPIError as error:
                        if _is_planka_card_not_found_error(error):
                            row.planka_card_id = ""
                            row.planka_card_url = ""
                            row.planka_pushed_at = None
                            row.planka_push_error = (
                                "Planka status: card deleted/archived in Planka. "
                                "It will be recreated on next push while staged."
                            )
                            missing_count += 1
                            should_unstage = False
                        else:
                            row.planka_push_error = f"Could not verify/remove Planka card: {_safe_planka_error(error)}"
                            failed_count += 1
                            should_unstage = False

                row.is_selected = (not should_unstage) if remove_from_planka else False
                if row.is_selected:
                    kept_staged_count += 1
                row.save(
                    update_fields=[
                        "is_selected",
                        "planka_card_id",
                        "planka_card_url",
                        "planka_pushed_at",
                        "planka_push_error",
                        "modified",
                    ]
                )

            if remove_from_planka and (removed_count or skipped_count or missing_count or failed_count):
                messages.info(
                    request,
                    (
                        f"Planka card cleanup: {removed_count} removed from Candidates, "
                        f"{skipped_count} moved, {missing_count} deleted/archived, {failed_count} failed, "
                        f"{kept_staged_count} kept staged."
                    ),
                )

    batch.selected_count = batch.batch_articles.filter(is_selected=True).count()
    batch.save(update_fields=["selected_count", "modified"])

    context_params = request.POST.copy()
    if action in {"select_all", "select_none"}:
        context_params["filter_selected"] = ""

    return _render_article_intake_results_response(request, batch, context_params)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_assign_issue(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    form = ArticleIntakeAssignIssueForm(request.POST)
    if form.is_valid():
        issue = form.cleaned_data.get("issue")
        batch.issue = issue
        batch.save(update_fields=["issue", "modified"])
        batch.batch_articles.update(issue=issue)
        messages.success(request, "Issue assignment updated for all fetched articles.")
    else:
        messages.error(request, "Could not update issue assignment.")

    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_check_for_new(request, batch_id):
    """Gated PubMed check: if cache is fresh, no API call; otherwise queue a refresh."""
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)

    watched_journals = list(batch.watched_journals.filter(active=True))
    if not watched_journals:
        messages.error(request, "No active watched journals on this batch.")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST)
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    remaining = _pubmed_fetch_gate_remaining(batch)
    if remaining is not None:
        minutes = max(1, int(remaining.total_seconds() // 60) + (1 if remaining.total_seconds() % 60 else 0))
        messages.info(
            request,
            f"PubMed was checked recently. Try again in about {minutes} minute(s).",
        )
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST)
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    _queue_batch_task(
        batch,
        action="check_for_new",
        note="Queued PubMed check for new articles.",
        task_callable=check_batch_for_new_articles_task,
        task_args=[batch.pk],
    )

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST)
    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_mark_all_seen(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    user_view = _get_or_create_user_view(batch, request.user)
    if user_view is not None:
        user_view.last_seen_at = timezone.now()
        user_view.seen_batch_article_ids = []
        user_view.save(update_fields=["last_seen_at", "seen_batch_article_ids", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST)
    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_mark_row_seen(request, batch_id, item_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    # Validate row belongs to batch.
    get_object_or_404(PubmedBatchArticle, pk=item_id, batch=batch)
    user_view = _get_or_create_user_view(batch, request.user)
    if user_view is not None:
        seen = list(user_view.seen_batch_article_ids or [])
        if item_id not in seen:
            seen.append(item_id)
            user_view.seen_batch_article_ids = seen
            user_view.save(update_fields=["seen_batch_article_ids", "modified"])
    return HttpResponse(status=204)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_push_to_planka(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    issue, binding, list_error = _get_issue_planka_candidates_list(batch, require_candidates_list=False)
    if list_error:
        messages.error(request, list_error)
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    push_scope = (request.POST.get("push_scope") or "selected").strip().lower()
    if _param_enabled(request.POST, "async", default=False):
        _queue_batch_task(
            batch,
            action="push",
            note="Queued push to Planka.",
            task_callable=run_pubmed_batch_push_task,
            task_args=[batch.pk, push_scope],
        )
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    if push_scope == "filtered":
        rows, *_ = _build_article_intake_queryset(batch, request.POST)
        row_ids = [row.pk for row in rows]
        target_rows = list(
            PubmedBatchArticle.objects.select_related("article", "issue").filter(batch=batch, pk__in=row_ids)
        )
    else:
        target_rows = list(batch.batch_articles.select_related("article", "issue").filter(is_selected=True))

    if not target_rows:
        messages.info(request, "No staged articles available to push.")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    try:
        client = shared._build_planka_client()
        planka_cards._ensure_planka_board_mappings(client=client, binding=binding)
        label_cache = planka_cards._get_board_label_map(client=client, board_id=binding.board_id)
        list_type_map = planka_cards._get_board_list_type_map(client=client, board_id=binding.board_id)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if "board not found" in safe_error.lower():
            messages.error(
                request,
                "Linked Planka board was not found. Re-link this issue to a valid Planka project/board.",
            )
        else:
            messages.error(request, f"Could not prepare Planka board: {safe_error}")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    candidates_list_id = binding.get_list_id("candidates")
    if not candidates_list_id:
        messages.error(request, "Candidates list is not configured for this Planka board.")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")
    created = 0
    already_pushed = 0
    failed = 0
    recreated_missing = 0
    fatal_list_missing = False

    for row in target_rows:
        if row.planka_card_id:
            try:
                existing_card = client.get_card(row.planka_card_id)
                if _is_planka_card_archived(existing_card):
                    row.planka_card_id = ""
                    row.planka_card_url = ""
                    row.planka_pushed_at = None
                    row.planka_push_error = "Planka status: previous card deleted/archived; recreating now."
                    row.save(
                        update_fields=[
                            "planka_card_id",
                            "planka_card_url",
                            "planka_pushed_at",
                            "planka_push_error",
                            "modified",
                        ]
                    )
                    recreated_missing += 1
                else:
                    existing_list_id = str(existing_card.get("listId") or "")
                    existing_list_type = list_type_map.get(existing_list_id, "")
                    if existing_list_type == "trash":
                        row.planka_card_id = ""
                        row.planka_card_url = ""
                        row.planka_pushed_at = None
                        row.planka_push_error = "Planka status: previous card deleted/archived; recreating now."
                        row.save(
                            update_fields=[
                                "planka_card_id",
                                "planka_card_url",
                                "planka_pushed_at",
                                "planka_push_error",
                                "modified",
                            ]
                        )
                        recreated_missing += 1
                    else:
                        if existing_list_id and existing_list_id != str(candidates_list_id):
                            row.planka_push_error = "Planka status: card moved from Candidates."
                            row.save(update_fields=["planka_push_error", "modified"])
                        elif row.planka_push_error:
                            row.planka_push_error = ""
                            row.save(update_fields=["planka_push_error", "modified"])
                        already_pushed += 1
                        continue
            except PlankaAPIError as error:
                if _is_planka_card_not_found_error(error):
                    row.planka_card_id = ""
                    row.planka_card_url = ""
                    row.planka_pushed_at = None
                    row.planka_push_error = "Planka status: previous card deleted/archived; recreating now."
                    row.save(
                        update_fields=[
                            "planka_card_id",
                            "planka_card_url",
                            "planka_pushed_at",
                            "planka_push_error",
                            "modified",
                        ]
                    )
                    recreated_missing += 1
                else:
                    row.planka_push_error = f"Could not verify existing Planka card: {_safe_planka_error(error)}"
                    row.save(update_fields=["planka_push_error", "modified"])
                    failed += 1
                    continue

        title, description = _build_pubmed_planka_card(row)
        try:
            card = client.create_card(candidates_list_id, title, description=description, card_type="project")
            card_id = str(card.get("id") or "").strip()
            _attach_journal_label_to_card(
                client=client,
                binding=binding,
                card_id=card_id,
                row=row,
                label_cache=label_cache,
            )
            row.planka_card_id = card_id
            base_url = (getattr(settings, "PLANKA_BASE_URL", "") or "").strip().rstrip("/")
            row.planka_card_url = f"{base_url}/cards/{card_id}" if base_url and card_id else ""
            row.planka_pushed_at = timezone.now()
            row.planka_push_error = ""
            row.save(
                update_fields=[
                    "planka_card_id",
                    "planka_card_url",
                    "planka_pushed_at",
                    "planka_push_error",
                    "modified",
                ]
            )
            created += 1
        except PlankaAPIError as error:
            row.planka_push_error = _safe_planka_error(error)
            row.save(update_fields=["planka_push_error", "modified"])
            failed += 1
            if _is_planka_list_not_found_error(error):
                fatal_list_missing = True
                break

    if fatal_list_missing:
        messages.error(
            request,
            "Candidates list was not found in Planka. Create or re-link a Planka board for this issue and try again.",
        )
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    if failed:
        messages.warning(
            request,
            (
                f"Push finished with issues: {created} created, "
                f"{already_pushed} already pushed, {recreated_missing} recreated missing, {failed} failed."
            ),
        )
    else:
        messages.success(
            request,
            (
                f"Push complete: {created} created, "
                f"{already_pushed} already pushed, {recreated_missing} recreated missing, {failed} failed."
            ),
        )

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST, message_target="push")

    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_reconcile_planka_status(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    staged_rows = list(batch.batch_articles.select_related("article", "issue").filter(is_selected=True))
    if not staged_rows:
        messages.info(request, "No staged articles to reconcile.")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    issue, binding, list_error = _get_issue_planka_candidates_list(batch, require_candidates_list=False)
    if list_error:
        messages.error(request, list_error)
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    try:
        client = shared._build_planka_client()
        planka_cards._ensure_planka_board_mappings(client=client, binding=binding)
        list_type_map = planka_cards._get_board_list_type_map(client=client, board_id=binding.board_id)
    except PlankaAPIError as error:
        messages.error(request, f"Could not prepare Planka board: {_safe_planka_error(error)}")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST, message_target="push")
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    candidates_list_id = str(binding.get_list_id("candidates") or "")
    candidates_count = 0
    moved_count = 0
    missing_count = 0
    unlinked_count = 0
    error_count = 0

    for row in staged_rows:
        if not row.planka_card_id:
            unlinked_count += 1
            continue

        try:
            card = client.get_card(row.planka_card_id)
            if _is_planka_card_archived(card):
                missing_count += 1
                row.planka_card_id = ""
                row.planka_card_url = ""
                row.planka_pushed_at = None
                row.planka_push_error = (
                    "Planka status: card deleted/archived in Planka. Ready to re-push while staged."
                )
                row.save(
                    update_fields=[
                        "planka_card_id",
                        "planka_card_url",
                        "planka_pushed_at",
                        "planka_push_error",
                        "modified",
                    ]
                )
            else:
                card_list_id = str(card.get("listId") or "")
                card_list_type = list_type_map.get(card_list_id, "")
                if card_list_type == "trash":
                    missing_count += 1
                    row.planka_card_id = ""
                    row.planka_card_url = ""
                    row.planka_pushed_at = None
                    row.planka_push_error = (
                        "Planka status: card deleted/archived in Planka. Ready to re-push while staged."
                    )
                    row.save(
                        update_fields=[
                            "planka_card_id",
                            "planka_card_url",
                            "planka_pushed_at",
                            "planka_push_error",
                            "modified",
                        ]
                    )
                elif candidates_list_id and card_list_id != candidates_list_id:
                    moved_count += 1
                    status_message = "Planka status: card moved from Candidates."
                    if row.planka_push_error != status_message:
                        row.planka_push_error = status_message
                        row.save(update_fields=["planka_push_error", "modified"])
                else:
                    candidates_count += 1
                    if row.planka_push_error:
                        row.planka_push_error = ""
                        row.save(update_fields=["planka_push_error", "modified"])
        except PlankaAPIError as error:
            if _is_planka_card_not_found_error(error):
                missing_count += 1
                row.planka_card_id = ""
                row.planka_card_url = ""
                row.planka_pushed_at = None
                row.planka_push_error = (
                    "Planka status: card deleted/archived in Planka. Ready to re-push while staged."
                )
                row.save(
                    update_fields=[
                        "planka_card_id",
                        "planka_card_url",
                        "planka_pushed_at",
                        "planka_push_error",
                        "modified",
                    ]
                )
            else:
                error_count += 1
                row.planka_push_error = f"Could not reconcile Planka card: {_safe_planka_error(error)}"
                row.save(update_fields=["planka_push_error", "modified"])

    messages.info(
        request,
        (
            f"Reconcile complete: {candidates_count} in Candidates, {moved_count} moved, "
            f"{missing_count} deleted/archived, {unlinked_count} unlinked, {error_count} errors."
        ),
    )
    if missing_count:
        messages.success(request, f"{missing_count} staged article(s) are ready to re-push.")

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST, message_target="push")

    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_task_status(request, batch_id):
    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)
    is_running = batch.task_state in {PubmedImportBatch.TASK_STATE_PENDING, PubmedImportBatch.TASK_STATE_RUNNING}
    is_done = batch.task_state in {PubmedImportBatch.TASK_STATE_SUCCESS, PubmedImportBatch.TASK_STATE_ERROR}
    channel = (request.GET.get("channel") or "stage").strip().lower()
    if channel == "push":
        container_id = "article-intake-push-task-status"
        poll_url = f"{reverse('backend:article_intake_task_status', kwargs={'batch_id': batch.pk})}?channel=push"
    else:
        container_id = "article-intake-task-status"
        poll_url = reverse("backend:article_intake_task_status", kwargs={"batch_id": batch.pk})

    context = {
        "batch": batch,
        "is_running": is_running,
        "is_done": is_done,
        "container_id": container_id,
        "poll_url": poll_url,
    }
    if is_done:
        if batch.task_state == PubmedImportBatch.TASK_STATE_ERROR:
            messages.error(request, batch.task_note)
        else:
            messages.success(request, batch.task_note)
        context.update(_article_intake_results_context(batch, request.GET, user=request.user))

    return render(request, "backend/_article_intake_task_status.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_recommended(request):
    """HTMX partial: recommended articles panel for the intake page."""
    from_month = request.GET.get("from_month", "")
    to_month = request.GET.get("to_month", "")
    show_all = request.GET.get("show_all") == "1"
    show_hidden = request.GET.get("show_hidden") == "1"
    batch_id = request.GET.get("batch_id", "")

    qs = (
        PubmedArticle.objects.filter(
            Q(user_states__recommended_at__isnull=False) | Q(visitor_recommendations__isnull=False),
            recommendation_hidden=show_hidden,
        )
        .annotate(
            recommendation_count=Count(
                "user_states",
                filter=Q(user_states__recommended_at__isnull=False),
                distinct=True,
            ),
            visitor_recommendation_count=Count("visitor_recommendations", distinct=True),
        )
        .distinct()
    )

    if not show_all and from_month and to_month:
        try:
            from_date = datetime.date.fromisoformat(from_month + "-01")
            to_date = datetime.date.fromisoformat(to_month + "-01")
            # Include the entire to_month
            to_date_end = _shift_month(to_date, 1) - datetime.timedelta(days=1)
            qs = qs.filter(publication_date__gte=from_date, publication_date__lte=to_date_end)
        except (ValueError, TypeError):
            pass

    recommended_articles = list(
        qs.order_by("-recommendation_count", "-visitor_recommendation_count", "-publication_date", "title")[:100]
    )

    # Attach staged/review indicators
    article_ids = [a.pk for a in recommended_articles]
    staged_ids = set()
    if batch_id.isdigit():
        staged_ids = set(
            PubmedBatchArticle.objects.filter(
                batch_id=int(batch_id),
                article_id__in=article_ids,
                is_selected=True,
            ).values_list("article_id", flat=True)
        )
    reviewed_ids = set(
        Review.objects.filter(article_id__in=article_ids).values_list("article_id", flat=True).distinct()
    )

    # Attach recommender names
    recommender_map = {}
    for state in PubmedArticleUserState.objects.filter(
        article_id__in=article_ids,
        recommended_at__isnull=False,
    ).select_related("user"):
        recommender_map.setdefault(state.article_id, []).append(str(state.user))

    for article in recommended_articles:
        article.is_staged = article.pk in staged_ids
        article.has_review = article.pk in reviewed_ids
        article.recommenders = recommender_map.get(article.pk, [])

    context = {
        "recommended_articles": recommended_articles,
        "show_all": show_all,
        "show_hidden": show_hidden,
        "from_month": from_month,
        "to_month": to_month,
        "batch_id": batch_id,
    }
    return render(request, "backend/_article_intake_recommended.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
@require_POST
def article_intake_toggle_recommendation_hidden(request, article_id):
    """Toggle recommendation_hidden on a PubmedArticle, then re-render the panel."""
    article = get_object_or_404(PubmedArticle, pk=article_id)
    article.recommendation_hidden = not article.recommendation_hidden
    PubmedArticle.objects.filter(pk=article.pk).update(recommendation_hidden=article.recommendation_hidden)

    # Build GET params from the POST data for re-rendering
    from django.http import QueryDict

    params = QueryDict(mutable=True)
    for key in ("from_month", "to_month", "show_all", "show_hidden", "batch_id"):
        val = request.POST.get(key, "")
        if val:
            params[key] = val
    request.GET = params
    return article_intake_recommended(request)
