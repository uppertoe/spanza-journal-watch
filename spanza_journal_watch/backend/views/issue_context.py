"""Context shared by every issue-scoped page (sidebar, workflow state, Planka summary)."""

import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from spanza_journal_watch.submissions.models import (
    HealthService,
    Issue,
)

from ..forms import (
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    IssueContributorInviteForm,
    PlankaProjectBackgroundForm,
    PlankaProjectNameForm,
    PlankaProjectSetupForm,
)
from ..models import (
    IssueContributor,
    PlankaBoardBackgroundAsset,
    PlankaIssueBinding,
    PlankaProjectSetupJob,
)
from ..planka import PlankaAPIError
from . import planka_boards, shared
from .planka_boards import _filter_board_cards_by_scope
from .shared import _get_planka_integration_credential

logger = logging.getLogger(__name__)


def _extract_publish_cards(binding):
    return _filter_board_cards_by_scope(planka_boards._extract_board_cards(binding), "publish")


def _build_card_payload_hash(selected_card):
    payload = {
        "id": selected_card.get("id"),
        "name": selected_card.get("name"),
        "schema": selected_card.get("schema") or {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_planka_publish_summary(publish_cards):
    cards = publish_cards or []
    total_cards = len(cards)
    valid_cards = sum(1 for card in cards if card.get("is_valid") and not card.get("has_associated_review"))
    missing_cards = sum(1 for card in cards if not card.get("is_valid"))
    already_imported_cards = sum(1 for card in cards if card.get("has_associated_review"))
    return {
        "total": total_cards,
        "valid": valid_cards,
        "missing": missing_cards,
        "already_imported": already_imported_cards,
    }


def _issue_invite_ttl_days():
    return int(getattr(settings, "ISSUE_CONTRIBUTOR_INVITE_TTL_DAYS", 180))


_PLANKA_PROJECT_PRIVATE_CACHE_TTL = 60
_PLANKA_PROJECT_PRIVATE_SENTINEL = object()


def _get_planka_project_private(project_id):
    """Return whether a Planka project is private, cached briefly.

    The builder pages render this indicator on every load; without caching we
    pay a synchronous HTTP round-trip to Planka on each request (~600ms cold).
    """
    cache_key = f"planka:project_private:{project_id}"
    cached = cache.get(cache_key, _PLANKA_PROJECT_PRIVATE_SENTINEL)
    if cached is not _PLANKA_PROJECT_PRIVATE_SENTINEL:
        return cached
    try:
        client = shared._build_planka_client()
        project = client.get_project(project_id)
        value = bool(project.get("ownerProjectManagerId"))
    except PlankaAPIError:
        value = None
    cache.set(cache_key, value, _PLANKA_PROJECT_PRIVATE_CACHE_TTL)
    return value


def _issue_builder_base_context(
    issue=None,
    review_form=None,
    form_action=None,
    is_edit=False,
    planka_publish_cards=None,
    planka_publish_summary=None,
    planka_panel_status=None,
    planka_panel_status_level="info",
    planka_background_form=None,
    planka_project_name_form=None,
    planka_context_status=None,
    planka_context_status_level="info",
    planka_disconnected=False,
    planka_card_scope="publish",
    planka_scope_counts=None,
    planka_board_missing=False,
    issue_contributor_invite_form=None,
):
    issue_qs = Issue.objects.only("id", "name", "active", "modified").order_by("-modified")
    credential = _get_planka_integration_credential()
    background_assets = PlankaBoardBackgroundAsset.objects.order_by("name")
    context = {
        "issues": issue_qs[:25],
        "selected_issue": issue,
        "issue_form": IssueBuilderIssueForm(instance=issue) if issue else IssueBuilderIssueForm(),
        "max_featured_reviews": int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2)),
        "planka_credential": credential,
        "planka_binding": None,
        "planka_setup_form": PlankaProjectSetupForm(),
        "planka_background_form": planka_background_form or PlankaProjectBackgroundForm(),
        "planka_project_name_form": planka_project_name_form or PlankaProjectNameForm(),
        "planka_background_assets": background_assets,
        "planka_publish_cards": planka_publish_cards,
        "planka_publish_summary": planka_publish_summary,
        "planka_panel_status": planka_panel_status,
        "planka_panel_status_level": planka_panel_status_level,
        "planka_disconnected": planka_disconnected,
        "planka_card_scope": planka_card_scope,
        "planka_scope_counts": planka_scope_counts or {"publish": 0, "all": 0},
        "planka_board_missing": planka_board_missing,
        "planka_context_status": planka_context_status,
        "planka_context_status_level": planka_context_status_level,
        "issue_contributors": [],
        "issue_coordinators": [],
        "issue_reviewers": [],
        "issue_contributor_invite_form": issue_contributor_invite_form or IssueContributorInviteForm(),
        "issue_invite_ttl_days": _issue_invite_ttl_days(),
        "all_health_services": list(HealthService.objects.order_by("name").values_list("name", flat=True)),
    }

    context["planka_setup_job"] = None
    if issue:
        binding = PlankaIssueBinding.objects.filter(issue=issue).first()
        context["planka_binding"] = binding
        planka_base = (
            getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "") or ""
        ).rstrip("/")
        context["planka_board_url"] = f"{planka_base}/boards/{binding.board_id}" if binding and planka_base else ""
        context["planka_setup_job"] = PlankaProjectSetupJob.objects.filter(issue=issue).first()
        context["planka_setup_form"] = PlankaProjectSetupForm(initial={"project_name": issue.name})
        if binding and binding.background_asset_id:
            context["planka_background_form"].fields["background_asset"].initial = binding.background_asset_id
        if binding:
            context["planka_project_name_form"].fields["project_name"].initial = binding.project_name
            context["planka_project_private"] = _get_planka_project_private(binding.project_id)

        context["review_form"] = review_form or IssueBuilderReviewForm(issue=issue)
        context["review_form_action"] = form_action or reverse(
            "backend:add_issue_review",
            kwargs={"issue_id": issue.pk},
        )
        context["suggest_headline_url"] = reverse("backend:suggest_review_headline", kwargs={"issue_id": issue.pk})
        context["review_form_is_edit"] = is_edit

        if context["planka_publish_cards"] is None:
            context["planka_publish_cards"] = []
        if context["planka_publish_summary"] is None:
            context["planka_publish_summary"] = _build_planka_publish_summary(context["planka_publish_cards"])

        all_contributors = IssueContributor.objects.filter(issue=issue).select_related("user", "invited_by", "author")
        context["issue_contributors"] = all_contributors
        context["issue_coordinators"] = [c for c in all_contributors if c.role == IssueContributor.Role.COORDINATOR]
        context["issue_reviewers"] = [c for c in all_contributors if c.role == IssueContributor.Role.REVIEWER]

    return context
