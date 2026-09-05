"""Importing publish-ready Planka cards into an issue, plus the card webhook and revisions."""

import datetime
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from spanza_journal_watch.submissions.models import (
    Author,
    Issue,
    Review,
)

from ..models import (
    PlankaCardImport,
    PlankaCardRevision,
    PlankaIssueBinding,
    PubmedArticle,
    PubmedBatchArticle,
)
from ..planka import PlankaAPIError
from . import planka_boards, shared
from .issue_context import _build_card_payload_hash
from .planka_boards import _build_planka_scope_counts, _filter_board_cards_by_scope
from .planka_cards import _build_pubmed_article_citation, _extract_planka_review_body
from .planka_panels import _render_planka_panel
from .shared import _bool_from_value, _is_planka_board_not_found_error, _is_planka_connection_error, _safe_planka_error

logger = logging.getLogger(__name__)


def _sync_planka_card_into_issue(*, request, issue, binding, selected):
    card_id = str(selected.get("id") or "").strip()
    if not card_id:
        return "danger", "Card id missing."

    existing_sync = (
        PlankaCardImport.objects.filter(card_id=card_id).select_related("review", "review__article").first()
    )
    if existing_sync and existing_sync.review_id:
        return "warning", "Review already created from this card. This card is protected and will not be re-imported."

    linked_batch_row = (
        PubmedBatchArticle.objects.select_related("article")
        .filter(issue=issue, planka_card_id=card_id)
        .order_by("-pk")
        .first()
    )
    if not linked_batch_row:
        linked_batch_row = (
            PubmedBatchArticle.objects.select_related("article").filter(planka_card_id=card_id).order_by("-pk").first()
        )

    if not linked_batch_row:
        # No planka_card_id link found. The batch that originally pushed this card
        # may have been deleted or regenerated — the link lives only on
        # PubmedBatchArticle, which is cascade-deleted with its batch, so the card
        # is orphaned from its cached article. Fall back to matching the card name
        # against pulled article titles (the pushed card title is the article
        # title verbatim). Prefer this issue's batch rows, then any batch row.
        # Require a unique match so we never guess; a bare re-import article is
        # never a batch row, so it can't collide here. Heal the link on success.
        card_name = (selected.get("name") or "").strip()
        if card_name:
            title_matches = list(
                PubmedBatchArticle.objects.select_related("article")
                .filter(issue=issue, article__title__iexact=card_name)
                .order_by("-pk")[:2]
            )
            if not title_matches:
                title_matches = list(
                    PubmedBatchArticle.objects.select_related("article")
                    .filter(article__title__iexact=card_name)
                    .order_by("-pk")[:2]
                )
            if len(title_matches) == 1:
                linked_batch_row = title_matches[0]
                if not linked_batch_row.planka_card_id:
                    linked_batch_row.planka_card_id = card_id
                    linked_batch_row.save(update_fields=["planka_card_id", "modified"])
            elif len(title_matches) > 1:
                messages.warning(
                    request,
                    f'Card "{card_name}" matched more than one pulled article by title; '
                    "the reviewer article was not auto-linked. Set the journal manually.",
                )

    schema = selected["schema"]
    source_article = linked_batch_row.article if linked_batch_row else None
    metadata_manual_review_required = source_article is None

    if source_article:
        # Reuse the cache-populated article so pmid/doi/abstract/mesh_terms
        # stay attached to the editorial row. Journal FK was linked at intake
        # by upsert_pubmed_article.
        article = source_article
        citation = _build_pubmed_article_citation(article)
        if citation and not article.citation:
            article.citation = citation
            article.save(update_fields=["citation"])
    else:
        # Card has no cached PubMed link — synthesise a bare article from the
        # card's schema fields.
        article_year = datetime.date.today().year
        article = PubmedArticle.objects.create(
            title=(selected.get("name") or "Untitled article").strip(),
            publication_date=datetime.date(article_year, 1, 1),
            citation="",
            article_url="",
            tags_string="",
            active=False,
        )

    # --- Resolve reviewer (review.author) ---
    # Primary:  card member(s) — the user(s) assigned to the card.
    # Fallback: the most-recent user who edited the card description (via actions).
    # In both cases we match the Planka user's email against IssueContributors for
    # this issue, since contributors have a linked Author profile.
    author = None

    client = shared._build_planka_client()
    try:
        memberships, member_users_by_id = client.get_card_members(card_id)
    except Exception:
        memberships, member_users_by_id = [], {}

    def _resolve_author_from_planka_user_ids(user_ids, users_by_id):
        """Try to match a list of Planka user IDs to an Author.

        Resolution order per user:
          1. Author with matching email field.
          2. IssueContributor for this issue with that email who already has an
             Author linked — use that Author.
          3. IssueContributor for this issue with that email — create an Author
             from the contributor's name and link it.
        Returns the first Author resolved, or None.
        """
        if len(user_ids) > 1:
            emails = [(users_by_id.get(uid) or {}).get("email", "") for uid in user_ids if uid]
            messages.warning(
                request,
                f"Multiple Planka card members found ({', '.join(e for e in emails if e)}). "
                "The first contributor match was used.",
            )
        for uid in user_ids:
            user_obj = users_by_id.get(str(uid) or "") or {}
            email = (user_obj.get("email") or "").strip().lower()
            if not email:
                continue

            # 1. Author record with this email.
            author_by_email = Author.objects.filter(email__iexact=email).first()
            if author_by_email:
                return author_by_email

            # 2 & 3. Match via IssueContributor for this issue.
            contributor = issue.contributors.select_related("author").filter(email__iexact=email).first()
            if contributor:
                if contributor.author:
                    return contributor.author
                # Create an Author from the contributor's name and link it.
                new_author = Author.objects.create(
                    name=contributor.name or user_obj.get("name") or email,
                    email=email,
                )
                contributor.author = new_author
                contributor.save(update_fields=["author", "modified"])
                return new_author

        return None

    member_user_ids = [str(m.get("userId") or "") for m in memberships if m.get("userId")]
    if member_user_ids:
        author = _resolve_author_from_planka_user_ids(member_user_ids, member_users_by_id)

    if author is None and member_user_ids:
        # Member found in Planka but not matched to a contributor — warn.
        emails = [(member_users_by_id.get(uid) or {}).get("email", uid) for uid in member_user_ids]
        messages.warning(
            request,
            f"Card member(s) ({', '.join(emails)}) could not be matched to a contributor "
            "for this issue. Reviewer was not set.",
        )

    if author is None and not member_user_ids:
        # No card member — try the most-recent description editor via actions.
        try:
            editor_ids = client.get_card_description_editor_ids(card_id)
        except Exception:
            editor_ids = []
        if editor_ids:
            # We only have user IDs here; fetch users to get emails.
            try:
                all_users = client.list_users()
                editor_users_by_id = {str(u.get("id") or ""): u for u in all_users if u.get("id")}
            except Exception:
                editor_users_by_id = {}
            author = _resolve_author_from_planka_user_ids(editor_ids, editor_users_by_id)

    parsed_description_review_body, used_separator = _extract_planka_review_body(selected.get("description"))
    incoming_review_body = parsed_description_review_body or (schema.get("review_body_markdown") or "").strip()
    missing_separator_needs_manual_review = bool((selected.get("description") or "").strip()) and not used_separator

    review = Review.objects.create(
        article=article,
        author=author,
        body=incoming_review_body,
        is_featured=_bool_from_value(schema.get("is_featured")),
        active=False,
    )
    issue.reviews.add(review)

    card_payload_hash = _build_card_payload_hash(selected)
    if existing_sync:
        existing_sync.issue = issue
        existing_sync.binding = binding
        existing_sync.card_name = selected["name"]
        existing_sync.review = review
        existing_sync.imported_by = request.user
        existing_sync.last_card_payload_hash = card_payload_hash
        existing_sync.last_review_modified_at = review.modified
        existing_sync.save()
    else:
        PlankaCardImport.objects.create(
            issue=issue,
            binding=binding,
            card_id=card_id,
            card_name=selected["name"],
            review=review,
            imported_by=request.user,
            last_card_payload_hash=card_payload_hash,
            last_review_modified_at=review.modified,
        )

    panel_status = "Review created from Planka card."
    panel_level = "success"

    if metadata_manual_review_required:
        panel_status = (
            f"{panel_status} No linked intake article was found for this card ID; "
            "article metadata needs manual verification."
        )
        panel_level = "warning"

    if missing_separator_needs_manual_review:
        panel_status = (
            f"{panel_status} Separator line was missing; review text was imported using fallback parsing. "
            "Please manually verify this review before publishing."
        )
        panel_level = "warning"

    return panel_level, panel_status


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_import_publish_cards_bulk(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_scope = (request.POST.get("card_scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"

    selection_mode = (request.POST.get("selection_mode") or "selected").strip().lower()

    try:
        board_cards = planka_boards._extract_board_cards(binding)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        board_missing = _is_planka_board_not_found_error(error)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=[],
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else "Linked Reviews board was not found in Planka. You can recreate the board for this issue."
                if board_missing
                else f"Could not fetch Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=board_missing,
        )

    cards_by_id = {str(item.get("id") or ""): item for item in board_cards}
    if selection_mode == "publish_bucket":
        target_cards = [item for item in board_cards if item.get("in_publish_ready")]
    else:
        selected_ids = [str(value).strip() for value in request.POST.getlist("card_ids") if str(value).strip()]
        target_cards = [cards_by_id[value] for value in selected_ids if value in cards_by_id]

    created = 0
    protected = 0
    skipped = 0
    warnings = []
    for card in target_cards:
        if card.get("has_associated_review"):
            protected += 1
            continue
        if not card.get("is_valid"):
            skipped += 1
            continue

        level, message = _sync_planka_card_into_issue(request=request, issue=issue, binding=binding, selected=card)
        if level == "success":
            created += 1
        elif level == "warning":
            warnings.append(message)
            if "protected" in message.lower():
                protected += 1
            else:
                created += 1
        else:
            skipped += 1
            warnings.append(message)

    refreshed_board_cards = planka_boards._extract_board_cards(binding)
    refreshed_cards = _filter_board_cards_by_scope(refreshed_board_cards, card_scope)
    panel_status = f"Bulk import complete: {created} created, {protected} protected, {skipped} skipped."
    panel_level = "success" if not warnings else "warning"
    if warnings:
        panel_status = f"{panel_status} {warnings[0]}"

    return _render_planka_panel(
        request,
        issue,
        publish_cards=refreshed_cards,
        panel_status=panel_status,
        panel_status_level=panel_level,
        planka_card_scope=card_scope,
        planka_scope_counts=_build_planka_scope_counts(refreshed_board_cards),
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_import_publish_card(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_scope = (request.POST.get("card_scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"
    card_id = (request.POST.get("card_id") or "").strip()
    if not card_id:
        return _render_planka_panel(
            request,
            issue,
            panel_status="Card id missing.",
            panel_status_level="danger",
            planka_card_scope=card_scope,
        )

    try:
        board_cards = planka_boards._extract_board_cards(binding)
        selected = next((item for item in board_cards if str(item.get("id") or "") == card_id), None)
        if not selected:
            return _render_planka_panel(
                request,
                issue,
                publish_cards=_filter_board_cards_by_scope(board_cards, card_scope),
                panel_status="Card not found on this board.",
                panel_status_level="danger",
                planka_card_scope=card_scope,
                planka_scope_counts=_build_planka_scope_counts(board_cards),
            )

        if selected.get("has_associated_review"):
            return _render_planka_panel(
                request,
                issue,
                publish_cards=_filter_board_cards_by_scope(board_cards, card_scope),
                panel_status=(
                    "Review already created from this card. This card is protected and will not be re-imported."
                ),
                panel_status_level="warning",
                planka_card_scope=card_scope,
                planka_scope_counts=_build_planka_scope_counts(board_cards),
            )
        panel_level, panel_status = _sync_planka_card_into_issue(
            request=request,
            issue=issue,
            binding=binding,
            selected=selected,
        )

        refreshed_board_cards = planka_boards._extract_board_cards(binding)
        refreshed_cards = _filter_board_cards_by_scope(refreshed_board_cards, card_scope)
        scope_counts = _build_planka_scope_counts(refreshed_board_cards)

        return _render_planka_panel(
            request,
            issue,
            publish_cards=refreshed_cards,
            panel_status=panel_status,
            panel_status_level=panel_level,
            planka_card_scope=card_scope,
            planka_scope_counts=scope_counts,
        )

    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        board_missing = _is_planka_board_not_found_error(error)
        return _render_planka_panel(
            request,
            issue,
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else "Linked Reviews board was not found in Planka. You can recreate the board for this issue."
                if board_missing
                else f"Could not fetch Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=board_missing,
        )
    except Exception as error:
        return _render_planka_panel(
            request,
            issue,
            panel_status=f"Sync failed: {_safe_planka_error(error)}",
            panel_status_level="danger",
        )


# ── Planka webhook receiver ────────────────────────────────────────────────────


@csrf_exempt
@require_POST
def planka_card_update_webhook(request):
    """
    Receives cardUpdate / cardCreate / cardDelete events from Planka.
    Planka payload: { event, data: { item, included }, prevData: { item }, user }
    Auth: requires Authorization: Bearer <secret>.
    """
    secret = (getattr(settings, "PLANKA_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        logger.error("PLANKA_WEBHOOK_SECRET is not set; rejecting Planka webhook request.")
        return JsonResponse({"detail": "Webhook misconfigured"}, status=503)

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, secret):
        return JsonResponse({"detail": "Forbidden"}, status=403)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Bad JSON"}, status=400)

    event = payload.get("event") or ""
    data = payload.get("data") or {}
    item = data.get("item") or {}
    prev_item = (payload.get("prevData") or {}).get("item") or {}

    card_id = str(item.get("id") or "")
    board_id = str(item.get("boardId") or "")
    if not card_id or not board_id:
        return JsonResponse({"ok": True})

    # Only record on description changes for cardUpdate; always snapshot for create.
    if event == "cardUpdate":
        new_desc = item.get("description") or ""
        old_desc = prev_item.get("description") or ""
        if new_desc == old_desc:
            return JsonResponse({"ok": True})
        description = new_desc
    elif event == "cardCreate":
        description = item.get("description") or ""
    else:
        return JsonResponse({"ok": True})

    binding = PlankaIssueBinding.objects.filter(board_id=board_id).first()
    if not binding:
        return JsonResponse({"ok": True})

    actor = payload.get("user") or {}
    actor_email = (actor.get("email") or "").strip()
    actor_name = f"{actor.get('firstName') or ''} {actor.get('lastName') or ''}".strip()

    PlankaCardRevision.record(
        binding=binding,
        card_id=card_id,
        card_name=item.get("name") or "",
        board_id=board_id,
        description=description,
        actor_email=actor_email,
        actor_name=actor_name,
        source="webhook",
    )

    return JsonResponse({"ok": True})


# ── Planka card revision history ───────────────────────────────────────────────


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_card_revisions(request, issue_id, card_id):
    """Return an HTML partial listing revisions for a given card."""
    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    revisions = list(PlankaCardRevision.objects.filter(binding=binding, card_id=card_id).order_by("-created")[:100])

    # Fetch the current live description from Planka.
    current_description = None
    current_card_name = None
    current_fetch_error = None
    try:
        client = shared._build_planka_client()
        card = client.get_card(card_id)
        current_description = card.get("description") or ""
        current_card_name = card.get("name") or ""
    except PlankaAPIError as exc:
        current_fetch_error = _safe_planka_error(exc)

    return render(
        request,
        "backend/issue_builder/_card_revisions_panel.html",
        {
            "issue": issue,
            "binding": binding,
            "card_id": card_id,
            "revisions": revisions,
            "current_description": current_description,
            "current_card_name": current_card_name,
            "current_fetch_error": current_fetch_error,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_card_revision_restore(request, issue_id, revision_id):
    """Restore a card description to the state saved in the given revision."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    revision = get_object_or_404(PlankaCardRevision, pk=revision_id, binding=binding)

    try:
        client = shared._build_planka_client()
        client._request("PATCH", f"/cards/{revision.card_id}", json={"description": revision.description})
    except PlankaAPIError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)

    return JsonResponse({"ok": True})
