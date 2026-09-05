"""Constants and helpers shared by more than one editorial view module."""

import logging
import re
from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.db import transaction

from spanza_journal_watch.submissions.models import (
    Issue,
)
from spanza_journal_watch.submissions.tasks import queue_indexnow_submission

from ..models import (
    IssueContributor,
    PlankaIntegrationCredential,
)
from ..planka import PlankaAPIError, PlankaClient

logger = logging.getLogger(__name__)

PLANKA_LIST_ORDER = [
    "candidates",
    "under_review",
    "publish_ready",
]

PLANKA_LIST_LABELS = {
    "candidates": "Candidates",
    "under_review": "Under review",
    "publish_ready": "Publish ready",
}

PLANKA_LIST_COLORS = {
    "candidates": "lagoon-blue",
    "under_review": "orange-peel",
    "publish_ready": "bright-moss",
}

PLANKA_INSTRUCTIONS_LIST_ORDER = ["reviewers", "editors", "administrators"]

PLANKA_INSTRUCTIONS_LIST_LABELS = {
    "reviewers": "Reviewers",
    "editors": "Editors",
    "administrators": "Administrators",
}

PLANKA_INSTRUCTIONS_LIST_COLORS = {
    "reviewers": "turquoise-sea",
    "editors": "pink-tulip",
    "administrators": "dark-granite",
}

PLANKA_INSTRUCTIONS_DIR = Path(__file__).resolve().parent.parent / "planka_instructions"

PLANKA_JOURNAL_LABEL_COLORS = [
    "berry-red",
    "pumpkin-orange",
    "lagoon-blue",
    "pink-tulip",
    "light-mud",
    "orange-peel",
    "bright-moss",
    "antique-blue",
    "dark-granite",
    "turquoise-sea",
    "summer-sky",
    "sweet-lilac",
    "modern-green",
    "pirate-gold",
]

PLANKA_REVIEW_SEPARATOR_MARKER = "< --- Please write your review below this line --- >"

PLANKA_REVIEW_INSTRUCTIONS = """\
**Before you begin:**

- **Add yourself as a member** of this card (use the Members section inside the card) so editors \
can see who is covering which article.
- Move the card to **Under Review** when you start writing.
- Move the card to **Publish Ready** when your review is complete.

A suggested review structure is provided below — feel free to use any format you prefer.

**Please do not edit the text of other reviewers.** Instead, use the **Comments** section at the \
bottom of this card to share feedback or ask questions.

If you lose work or accidentally overwrite content, contact your regional coordinator — \
previous versions of this card can be restored.\
"""

PLANKA_REVIEW_SCAFFOLD = """## Review summary

## Key findings

## Strengths

## Limitations

## Bottom line"""

PLANKA_LEGACY_REVIEW_DESCRIPTION_TEMPLATE = """## Review summary

## Key findings

## Strengths

## Limitations

## Bottom line
"""


def _bool_from_value(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_planka_error(error):
    text = str(error or "").strip()
    if not text:
        return "Planka request failed."

    redacted = re.sub(r"(?i)(authorization\s*[:=]\s*)(bearer\s+[^\s,;]+)", r"\1[REDACTED]", text)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(password\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(token\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)

    return redacted[:500]


def _is_planka_connection_error(error):
    text = _safe_planka_error(error).lower()
    if not text:
        return False

    indicators = (
        "could not connect to planka",
        "connection refused",
        "failed to establish a new connection",
        "name or service not known",
        "temporary failure in name resolution",
        "timed out",
        "max retries exceeded",
        "connection aborted",
        "connection reset",
    )
    return any(marker in text for marker in indicators)


def _is_planka_list_not_found_error(error):
    text = _safe_planka_error(error).lower()
    if not text:
        return False
    return "list not found" in text or "e_not_found" in text


def _is_planka_board_not_found_error(error):
    text = _safe_planka_error(error).lower()
    if not text:
        return False
    return "board not found" in text or "planka api 404" in text or "e_not_found" in text


def _is_planka_card_not_found_error(error):
    text = _safe_planka_error(error).lower()
    if not text:
        return False
    markers = (
        "card not found",
        "record not found",
        "item not found",
        "e_not_found",
        "planka api 404",
        "http 404",
    )
    if any(marker in text for marker in markers):
        return True

    # Fallback for payloads that only return a generic not-found message.
    return "not found" in text


def _is_planka_card_archived(card):
    if not isinstance(card, dict):
        return False

    def _is_truthy(value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    if _is_truthy(card.get("isArchived")):
        return True

    for key in ("archivedAt", "deletedAt", "removedAt"):
        if str(card.get(key) or "").strip():
            return True

    if _is_truthy(card.get("isDeleted")):
        return True

    # In some Planka versions, archived cards are exposed as closed cards.
    if _is_truthy(card.get("isClosed")):
        return True

    return False


def _is_coordinator_only(user):
    """Return True if the user is a regional coordinator without chief-editor privileges."""
    return user.has_perm("submissions.regional_coordinator") and not user.has_perm("submissions.chief_editor")


def _check_coordinator_issue_access(request, issue):
    """Raise PermissionDenied if a coordinator-only user is not assigned to this issue."""
    if _is_coordinator_only(request.user):
        if issue is None:
            raise PermissionDenied
        if not IssueContributor.objects.filter(
            user=request.user,
            issue=issue,
            role=IssueContributor.Role.COORDINATOR,
            status=IssueContributor.Status.ACTIVE,
        ).exists():
            raise PermissionDenied


def _resolve_and_persist_issue(request, *, fallback_latest=True):
    """Resolve the selected issue, persisting the choice to the session.

    Priority: ?issue=new (clear) > ?issue=<id> > session > most-recently-modified issue.
    """
    issue_id = (request.GET.get("issue") or request.POST.get("issue") or "").strip()
    if issue_id.lower() == "new":
        request.session.pop("selected_issue_id", None)
        return None
    if issue_id.isdigit():
        issue = Issue.objects.filter(pk=issue_id).first()
        if issue:
            request.session["selected_issue_id"] = issue.pk
            return issue

    session_id = request.session.get("selected_issue_id")
    if session_id:
        issue = Issue.objects.filter(pk=session_id).first()
        if issue:
            return issue

    if fallback_latest:
        issue = Issue.objects.order_by("-modified", "-pk").first()
        if issue:
            request.session["selected_issue_id"] = issue.pk
        return issue
    return None


def _get_planka_integration_credential():
    return PlankaIntegrationCredential.get_solo()


def _build_planka_client():
    credential = _get_planka_integration_credential()
    if credential and credential.api_key:
        client = PlankaClient(api_key=credential.get_api_key(), access_token="")
    else:
        client = PlankaClient()

    if not client.configured:
        raise PlankaAPIError(
            "Planka is not configured. Add integration credentials or set PLANKA_BASE_URL and PLANKA_API_KEY."
        )

    return client


def _queue_indexnow_for_review(review):
    """Ask search engines to re-crawl a live review after it is edited in place."""
    if not review or not review.active:
        return
    paths = [review.get_absolute_url()]
    if review.author and not review.author.anonymous:
        paths.append(review.author.get_absolute_url())
    transaction.on_commit(lambda: queue_indexnow_submission(paths))
