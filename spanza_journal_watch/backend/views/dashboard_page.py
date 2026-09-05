"""The editorial dashboard and the sign-in landing redirect."""

import datetime
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import (
    Issue,
    Review,
)

from ..models import (
    FetchLog,
    IssueContributor,
    PlankaIssueBinding,
    PubmedBatchArticle,
)
from .analytics_page import _safe_percentage
from .shared import _get_planka_integration_credential

logger = logging.getLogger(__name__)


@login_required
@permission_required("submissions.invited_contributor", raise_exception=True)
def backend_go(request):
    """
    Role-aware destination chooser for editorial users.
    Requires invited_contributor permission (granted to all invited reviewers,
    coordinators, and chief editors).
    - Reviewers (no backend perms) → redirect straight to Planka.
    - Chief editors / regional coordinators → show Backend + Planka choice.
    """
    planka_url = getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "")
    has_backend = request.user.has_perm("submissions.chief_editor") or request.user.has_perm(
        "submissions.regional_coordinator"
    )

    if not has_backend:
        # Pure reviewer — send straight to Planka
        if planka_url:
            return redirect(planka_url)
        return redirect("/")

    # Collect the user's assigned issues (as coordinator) for context
    assigned_issues = []
    if request.user.has_perm("submissions.regional_coordinator") and not request.user.has_perm(
        "submissions.chief_editor"
    ):
        assigned_issues = list(
            IssueContributor.objects.filter(
                user=request.user,
                role=IssueContributor.Role.COORDINATOR,
                status=IssueContributor.Status.ACTIVE,
            )
            .select_related("issue")
            .order_by("-issue__modified")
        )

    return render(
        request,
        "backend/backend_go.html",
        {
            "planka_url": planka_url,
            "is_chief_editor": request.user.has_perm("submissions.chief_editor"),
            "assigned_issues": assigned_issues,
        },
    )


@login_required
def dashboard(request):
    from spanza_journal_watch.layout.models import Homepage

    is_coordinator_only = request.user.has_perm("submissions.regional_coordinator") and not request.user.has_perm(
        "submissions.chief_editor"
    )
    if not is_coordinator_only and not request.user.has_perm("backend.manage_subscriber_csv"):
        raise PermissionDenied

    if is_coordinator_only:
        assigned = list(
            IssueContributor.objects.filter(
                user=request.user,
                role=IssueContributor.Role.COORDINATOR,
                status=IssueContributor.Status.ACTIVE,
            )
            .select_related("issue")
            .order_by("-issue__modified")
        )
        planka_url = getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "")
        return render(
            request,
            "backend/dashboard_coordinator.html",
            {
                "assigned_contributors": assigned,
                "planka_url": planka_url,
            },
        )

    def _pct_change(current, previous):
        if not previous:
            return None
        return round((current - previous) / previous * 100)

    now = timezone.now()
    seven_days_ago = now - datetime.timedelta(days=7)
    fourteen_days_ago = now - datetime.timedelta(days=14)

    current_homepage = Homepage.get_current_homepage()
    current_issue = Issue.objects.order_by("-modified").first()
    current_issue_review_count = current_issue.reviews.count() if current_issue else 0

    # ── Health strip ──────────────────────────────────────────────
    planka_credential = _get_planka_integration_credential()
    planka_api_key_ok = bool(planka_credential and planka_credential.get_api_key())
    planka_connection_error = (planka_credential.last_error or "") if planka_credential else ""
    try:
        from oauth2_provider.models import Application as OAuthApplication

        planka_oidc_ok = OAuthApplication.objects.filter(client_id__startswith="planka").exists()
    except Exception:
        planka_oidc_ok = False

    planka_ok = planka_oidc_ok and planka_api_key_ok and not planka_connection_error

    last_fetch = FetchLog.objects.filter(status=FetchLog.STATUS_SUCCESS).order_by("-started_at").first()
    fetch_hours_ago = None
    if last_fetch and last_fetch.started_at:
        fetch_hours_ago = round((now - last_fetch.started_at).total_seconds() / 3600, 1)

    latest_newsletter = Newsletter.objects.select_related("issue").order_by("-pk").first()
    subscriber_count = Subscriber.objects.filter(subscribed=True).count()

    # ── At-a-glance metrics (7-day window) ────────────────────────
    review_ct = ContentType.objects.get_for_model(Review)
    recent_human = AnalyticsEvent.objects.filter(timestamp__gte=seven_days_ago, automated=False)
    prev_human = AnalyticsEvent.objects.filter(
        timestamp__gte=fourteen_days_ago, timestamp__lt=seven_days_ago, automated=False
    )

    visitors_7d = recent_human.exclude(visitor_id=None).values("visitor_id").distinct().count()
    engaged_7d = recent_human.filter(
        event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED, content_type=review_ct
    ).count()
    engaged_prev = prev_human.filter(
        event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED, content_type=review_ct
    ).count()
    engaged_delta = _pct_change(engaged_7d, engaged_prev)

    opens_7d = recent_human.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN, content_type=review_ct).count()
    full_text_7d = recent_human.filter(
        event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK, content_type=review_ct
    ).count()
    full_text_ctr = _safe_percentage(full_text_7d, opens_7d) if opens_7d else "—"

    searches_7d = (
        recent_human.filter(event_type=AnalyticsEvent.EventType.SEARCH)
        .exclude(metadata__query="")
        .exclude(metadata__query__isnull=True)
        .count()
    )

    # ── Issue workflow progress ───────────────────────────────────
    workflow_steps = []
    if current_issue:
        has_board = PlankaIssueBinding.objects.filter(issue=current_issue).exists()
        has_articles = PubmedBatchArticle.objects.filter(batch__issue=current_issue, is_selected=True).exists()
        # Coordinators are contributors too; only reviewers count for this step.
        has_reviewers = (
            IssueContributor.objects.filter(issue=current_issue, role=IssueContributor.Role.REVIEWER)
            .exclude(status=IssueContributor.Status.REVOKED)
            .exists()
        )
        has_reviews = current_issue.reviews.filter(active=True).exists()
        is_published = current_issue.active
        issue_newsletter = Newsletter.objects.filter(issue=current_issue).first()
        newsletter_sent = issue_newsletter.is_sent if issue_newsletter else False

        workflow_steps = [
            ("Setup", has_board),
            ("Articles", has_articles),
            ("Reviewers", has_reviewers),
            ("Reviews", has_reviews),
            ("Published", is_published),
            ("Newsletter", newsletter_sent),
        ]

    # ── Action items ──────────────────────────────────────────────
    action_items = []
    if current_issue and current_issue.active:
        issue_nl = Newsletter.objects.filter(issue=current_issue).first()
        if not issue_nl or not issue_nl.is_sent:
            action_items.append(
                {
                    "text": f"Newsletter not yet sent for {current_issue.name}",
                    "url": reverse("backend:newsletter_release_list") + f"?issue={current_issue.pk}",
                }
            )

    return render(
        request,
        "backend/dashboard.html",
        {
            "current_homepage": current_homepage,
            "current_issue": current_issue,
            "current_issue_review_count": current_issue_review_count,
            # Health strip
            "planka_ok": planka_ok,
            "planka_connection_error": planka_connection_error,
            "fetch_hours_ago": fetch_hours_ago,
            "latest_newsletter": latest_newsletter,
            "subscriber_count": subscriber_count,
            # Metrics
            "visitors_7d": visitors_7d,
            "engaged_7d": engaged_7d,
            "engaged_delta": engaged_delta,
            "full_text_ctr": full_text_ctr,
            "searches_7d": searches_7d,
            # Workflow
            "workflow_steps": workflow_steps,
            "action_items": action_items,
        },
    )
