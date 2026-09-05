"""Planka board plumbing: webhooks, snapshots, member sync and card extraction."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from ..models import (
    IssueContributor,
    PlankaCardRevision,
    PlankaIssueBinding,
)
from ..planka import PlankaAPIError
from . import shared
from .planka_cards import _decode_planka_escaped_text, _parse_planka_card_metadata
from .shared import _safe_planka_error

logger = logging.getLogger(__name__)


def _build_planka_webhook_url():
    """Return the absolute URL Planka should POST card-update events to."""
    base = (getattr(settings, "PLANKA_CALLBACK_BASE_URL", "") or "").rstrip("/")
    path = reverse("backend:planka_card_update_webhook")
    return f"{base}{path}"


def _register_planka_webhook(client, binding):
    """
    Ensure a global Planka webhook exists for our callback URL.
    Planka webhooks are not board-scoped, so one webhook covers all boards.
    If one already exists pointing to our URL, reuses it.
    Saves webhook_id on the binding and takes an initial board snapshot.
    Logs but does not raise on failure.
    """
    callback_url = _build_planka_webhook_url()
    if not callback_url.startswith("http"):
        logger.warning("PLANKA_CALLBACK_BASE_URL is not set; skipping webhook registration.")
        return
    secret = (getattr(settings, "PLANKA_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        logger.warning("PLANKA_WEBHOOK_SECRET is not set; skipping webhook registration.")
        return

    # Check if a webhook for our URL already exists (created for a previous binding).
    try:
        existing = client.list_webhooks()
        for wh in existing:
            if wh.get("url") == callback_url:
                webhook_id = str(wh.get("id") or "")
                binding.webhook_id = webhook_id
                binding.save(update_fields=["webhook_id", "modified"])
                _take_board_description_snapshot(client, binding)
                return
    except PlankaAPIError as exc:
        logger.error("Could not list Planka webhooks: %s", exc)

    try:
        webhook = client.create_webhook(
            callback_url,
            events=["cardUpdate", "cardCreate", "cardDelete"],
            access_token=secret,
        )
        binding.webhook_id = str(webhook.get("id") or "")
        binding.save(update_fields=["webhook_id", "modified"])
        _take_board_description_snapshot(client, binding)
    except PlankaAPIError as exc:
        logger.error("Could not register Planka webhook: %s", exc)


def _take_board_description_snapshot(client, binding):
    """
    Fetch all cards on the Reviews board and record an initial description
    snapshot for each. Skips cards with no description or no change.
    """
    try:
        _board_item, included = client.get_board(binding.board_id)
    except PlankaAPIError as exc:
        logger.error("Could not fetch board %s for snapshot: %s", binding.board_id, exc)
        return

    included = included or {}
    cards = included.get("cards") or []
    lists_by_id = {lst["id"]: lst for lst in (included.get("lists") or [])}

    for card in cards:
        card_id = str(card.get("id") or "")
        if not card_id:
            continue
        description = card.get("description") or ""
        list_id = str(card.get("listId") or "")
        list_obj = lists_by_id.get(list_id, {})
        list_type = list_obj.get("type") or "active"
        if list_type == "trash":
            continue
        PlankaCardRevision.record(
            binding=binding,
            card_id=card_id,
            card_name=card.get("name") or "",
            board_id=binding.board_id,
            description=description,
            source="snapshot",
        )


def _sync_contributor_to_planka(contributor):
    """
    Ensure the contributor has a Planka user account and is a member of the
    issue's Planka board. Updates and saves the planka_* fields on the contributor.
    Returns (success: bool, error_message: str).
    """
    try:
        binding = PlankaIssueBinding.objects.filter(issue=contributor.issue).first()
        if not binding:
            return False, "No Planka board is linked to this issue."

        client = shared._build_planka_client()

        planka_user = client.find_user_by_email(contributor.email)

        desired_name = (contributor.name or "").strip() or contributor.email
        if not planka_user:
            try:
                planka_user = client.create_user(contributor.email, desired_name)
            except PlankaAPIError as exc:
                if "403" in str(exc):
                    # OIDC_ENFORCED=true blocks the REST API — create via direct DB write
                    planka_user = client.create_user_via_db(contributor.email, desired_name)
                else:
                    raise
        elif (planka_user.get("name") or "").strip() != desired_name:
            try:
                client.update_user(str(planka_user["id"]), desired_name)
            except PlankaAPIError:
                pass  # Best-effort: OIDC will sync the name on next Planka login

        # If a Django user already exists for this email, ensure user.name is
        # set so the OIDC token carries the correct name when they log into Planka.
        User = get_user_model()
        django_user = User.objects.filter(email__iexact=contributor.email).first()
        if django_user and not (getattr(django_user, "name", "") or "").strip():
            django_user.name = desired_name
            django_user.save(update_fields=["name"])

        planka_user_id = str(planka_user["id"])

        # Remove stale memberships before (re-)adding
        for stale_id in [contributor.planka_membership_id, contributor.planka_instructions_membership_id]:
            if stale_id:
                try:
                    client.remove_board_member(stale_id)
                except PlankaAPIError:
                    pass

        # Reviews board — editor so the contributor can edit cards
        membership = client.add_board_member(binding.board_id, planka_user_id, role="editor")
        membership_id = str(membership.get("id", ""))

        # Instructions board — coordinators get editor access; others get read-only viewer
        instructions_membership_id = ""
        if binding.instructions_board_id:
            instructions_role = "editor" if contributor.role == IssueContributor.Role.COORDINATOR else "viewer"
            instr_membership = client.add_board_member(
                binding.instructions_board_id, planka_user_id, role=instructions_role
            )
            instructions_membership_id = str(instr_membership.get("id", ""))

        contributor.planka_user_id = planka_user_id
        contributor.planka_membership_id = membership_id
        contributor.planka_instructions_membership_id = instructions_membership_id
        contributor.planka_sync_state = IssueContributor.PlankaSyncState.OK
        contributor.planka_last_error = ""
        contributor.save(
            update_fields=[
                "planka_user_id",
                "planka_membership_id",
                "planka_instructions_membership_id",
                "planka_sync_state",
                "planka_last_error",
                "modified",
            ]
        )
        return True, ""

    except PlankaAPIError as error:
        error_msg = _safe_planka_error(error)
        contributor.planka_sync_state = IssueContributor.PlankaSyncState.ERROR
        contributor.planka_last_error = error_msg
        contributor.save(update_fields=["planka_sync_state", "planka_last_error", "modified"])
        return False, error_msg


def _extract_board_cards(binding):
    client = shared._build_planka_client()
    _, included = client.get_board(binding.board_id)

    lists = included.get("lists", []) or []
    cards = included.get("cards", []) or []
    lists_by_id = {str(item.get("id") or ""): item for item in lists if str(item.get("id") or "").strip()}

    publish_list_id = binding.get_list_id("publish_ready")
    if not publish_list_id:
        publish_list = next(
            (item for item in lists if str(item.get("name", "")).strip().lower() in {"publish ready", "publish"}),
            None,
        )
        publish_list_id = publish_list.get("id") if publish_list else None
    publish_list_id = str(publish_list_id or "")

    imports_by_card = {item.card_id: item for item in binding.imports.select_related("review").all()}
    board_cards = []
    for card in cards:
        decoded_description = _decode_planka_escaped_text(card.get("description"))
        card_schema = _parse_planka_card_metadata(decoded_description)
        card_schema["article_name"] = (card.get("name") or "(Untitled card)").strip()
        card_schema.setdefault("tags_string", "")
        card_schema.setdefault("author_name", "")
        card_schema.setdefault("author_title", "")
        card_schema.setdefault("review_body_markdown", "")
        card_schema.setdefault("is_featured", "")

        missing_required = []

        card_id = card.get("id")
        existing_sync = imports_by_card.get(card_id)
        has_associated_review = bool(existing_sync and existing_sync.review_id)
        list_id = str(card.get("listId") or "")
        list_obj = lists_by_id.get(list_id) or {}

        board_cards.append(
            {
                "id": card_id,
                "name": card.get("name") or "(Untitled card)",
                "description": decoded_description.strip(),
                "schema": card_schema,
                "missing_required": missing_required,
                "is_valid": True,
                "already_imported": bool(existing_sync),
                "has_associated_review": has_associated_review,
                "associated_review_id": existing_sync.review_id if existing_sync else None,
                "sync_blocked_reason": "Review already created from this card." if has_associated_review else "",
                "list_id": list_id,
                "list_name": str(list_obj.get("name") or "").strip() or "(Unnamed list)",
                "list_type": str(list_obj.get("type") or "").strip().lower(),
                "in_publish_ready": bool(publish_list_id and list_id == publish_list_id),
            }
        )

    return sorted(board_cards, key=lambda item: item["name"].lower())


def _filter_board_cards_by_scope(board_cards, scope):
    normalized = str(scope or "publish").strip().lower()
    cards = board_cards or []
    if normalized == "all":
        return cards
    return [item for item in cards if item.get("in_publish_ready")]


def _build_planka_scope_counts(board_cards):
    cards = board_cards or []
    publish_cards = [item for item in cards if item.get("in_publish_ready")]
    return {
        "publish": len(publish_cards),
        "all": len(cards),
    }
