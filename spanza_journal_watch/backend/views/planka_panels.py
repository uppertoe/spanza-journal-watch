"""HTMX panel renderers for the Planka section of the issue builder."""

import logging

from django.shortcuts import render

from .issue_context import _build_planka_publish_summary, _issue_builder_base_context

logger = logging.getLogger(__name__)


def _render_planka_panel(
    request,
    issue,
    publish_cards=None,
    panel_status=None,
    panel_status_level="info",
    planka_disconnected=False,
    planka_card_scope="publish",
    planka_scope_counts=None,
    planka_board_missing=False,
):
    cards = publish_cards if publish_cards is not None else []
    context = _issue_builder_base_context(
        issue=issue,
        planka_publish_cards=cards,
        planka_publish_summary=_build_planka_publish_summary(cards),
        planka_panel_status=panel_status,
        planka_panel_status_level=panel_status_level,
        planka_disconnected=planka_disconnected,
        planka_card_scope=planka_card_scope,
        planka_scope_counts=planka_scope_counts,
        planka_board_missing=planka_board_missing,
    )
    return render(request, "backend/issue_builder/_planka_panel.html", context)


def _render_planka_project_context_card(request, issue, card_status=None, card_status_level="info"):
    context = _issue_builder_base_context(
        issue=issue,
        planka_context_status=card_status,
        planka_context_status_level=card_status_level,
    )
    return render(request, "backend/issue_builder/_planka_project_context_card.html", context)
