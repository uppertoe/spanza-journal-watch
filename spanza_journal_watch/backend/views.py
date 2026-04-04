import datetime
import hashlib
import hmac
import io
import json
import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import MultipleObjectsReturned, PermissionDenied
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.static import serve as static_serve
from PIL import Image, UnidentifiedImageError

from spanza_journal_watch.analytics.models import AnalyticsEvent, NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.newsletter.tasks import send_newsletter, send_newsletter_test_email
from spanza_journal_watch.submissions.models import (
    Author,
    CuratedCollection,
    HealthService,
    Issue,
    Journal,
    MeshTagMapping,
    Review,
    Tag,
)
from spanza_journal_watch.utils.cache import bump_content_cache_version

from .forms import (
    ArticleIntakeAssignIssueForm,
    ArticleIntakeFetchForm,
    AuthorForm,
    BackendPreferenceFrontendBannerForm,
    BackendPreferenceInboxSettingsForm,
    HeaderForm,
    HealthServiceForm,
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    IssueContributorInviteForm,
    NewsletterCreateForm,
    NewsletterEditForm,
    NewsletterTestSendForm,
    PlankaProjectBackgroundForm,
    PlankaProjectNameForm,
    PlankaProjectSetupForm,
    PubmedApiKeyForm,
    SubscriberCSVForm,
    WatchedJournalForm,
    peek_csv,
)
from .models import (
    BackendPreference,
    ChiefEditorInvite,
    FetchLog,
    IssueContributor,
    IssueContributorInvite,
    PlankaBoardBackgroundAsset,
    PlankaCardImport,
    PlankaCardRevision,
    PlankaIntegrationCredential,
    PlankaIssueBinding,
    PubmedArticle,
    PubmedArticleUserState,
    PubmedBatchArticle,
    PubmedImportBatch,
    PubmedIntegrationCredential,
    SubscriberCSV,
    WatchedJournal,
)
from .planka import PlankaAPIError, PlankaClient
from .pubmed import PubmedAPIError
from .pubmed_cache import (
    article_matches_metadata as _article_matches_metadata,
)
from .pubmed_cache import (
    article_matches_topic as _article_matches_topic,
)
from .pubmed_cache import (
    build_pubmed_client as _build_pubmed_client,
)
from .pubmed_cache import (
    fill_missing_article_metadata as _fill_missing_article_metadata,
)
from .pubmed_cache import (
    populate_pubmed_batch_from_cache,
)
from .pubmed_cache import (
    shift_month as _shift_month,
)
from .tasks import (
    process_subscriber_csv,
    refresh_pubmed_journal_cache_task,
    run_pubmed_batch_import_task,
    run_pubmed_batch_push_task,
)

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

PLANKA_INSTRUCTIONS_DIR = Path(__file__).resolve().parent / "planka_instructions"

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


def _parse_instruction_cards(markdown_text):
    cards = []
    current_title = None
    current_body = []

    for line in (markdown_text or "").splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            if current_title:
                cards.append({"title": current_title, "body": "\n".join(current_body).strip()})
            current_title = heading_match.group(1).strip()
            current_body = []
            continue
        current_body.append(line)

    if current_title:
        cards.append({"title": current_title, "body": "\n".join(current_body).strip()})

    return [card for card in cards if card["title"]]


def _load_instruction_cards_by_bucket():
    cards_by_bucket = {}
    for bucket in PLANKA_INSTRUCTIONS_LIST_ORDER:
        path = PLANKA_INSTRUCTIONS_DIR / f"{bucket}.md"
        if not path.exists():
            cards_by_bucket[bucket] = []
            continue
        cards_by_bucket[bucket] = _parse_instruction_cards(path.read_text(encoding="utf-8"))

    return cards_by_bucket


def _normalize_background_to_webp(uploaded_file):
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image = image.convert("RGB")
            image.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=90, method=6)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Uploaded file is not a valid image.") from error


def _resolve_background_asset(form, user):
    selected_asset = form.cleaned_data.get("background_asset")
    uploaded_file = form.cleaned_data.get("background_upload")

    if uploaded_file:
        webp_bytes = _normalize_background_to_webp(uploaded_file)
        filename_slug = slugify(Path(uploaded_file.name).stem) or "background"
        asset = PlankaBoardBackgroundAsset(
            name=f"{filename_slug} ({timezone.now().strftime('%Y-%m-%d %H:%M')})",
            uploaded_by=user,
        )
        asset.image.save(
            f"{filename_slug}-{timezone.now().strftime('%Y%m%d%H%M%S')}.webp", ContentFile(webp_bytes), save=True
        )
        return asset

    return selected_asset


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def upload_subscriber_csv(request):
    context = {}

    if request.method == "POST":
        form = SubscriberCSVForm(request.POST, request.FILES)
        context["form"] = form

        if form.is_valid():
            instance = form.save(commit=False)
            header = form.cleaned_data["has_header"]
            instance.header = header  # Save the csv sniffer best guess
            instance.save()

            context["instance"] = instance
            context["preview"] = form.cleaned_data["preview"]
            context["header_form"] = HeaderForm(initial={"header": header})  # include a checkbox for header select

            # HTMX not yet implemented here
            if request.headers.get("HX-Request") == "true":
                template = "backend/preview_csv_htmx.html"
            else:
                template = "backend/preview_csv.html"

            return render(request, template, context)

    else:
        form = SubscriberCSVForm()
        context["form"] = form

    return render(request, "backend/upload_subscribers.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def edit_csv_header(request, save_token):
    # Requires HTMX
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request - HTMX only")

    # Perform a lookup using the token
    try:
        subscriber_csv = SubscriberCSV.objects.get(save_token=save_token)
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned):
        messages.error(request, "There was a problem updating this CSV. Please refresh the page and try again")
        return render(request, "fragments/messages.html")

    if request.method == "POST":
        form = HeaderForm(request.POST)

        if form.is_valid():
            header = form.cleaned_data["header"]
            logger.debug("CSV header set to: %s", header)
            subscriber_csv.header = header
            subscriber_csv.save()

    else:
        form = HeaderForm(initial={"header": subscriber_csv.header})

    # Re-peek into the CSV
    file = subscriber_csv.file.open()
    peek = peek_csv(file, user_header=subscriber_csv.header)
    file.close()

    context = {"header_form": form, "instance": subscriber_csv}
    context.update(peek)

    return render(request, "backend/preview_csv_htmx.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def process_csv(request, save_token):
    """
    Accessing this endpoint sets the subscriber_csv.confirmed to True
    Saving the object then sends the task to Celery for processing

    Requires a subscriber_csv.save_token
    """
    # Requires HTMX
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request - HTMX only")

    # Perform a lookup using the token
    try:
        subscriber_csv = SubscriberCSV.objects.get(save_token=save_token)
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned):
        messages.error(request, "There was a problem updating this CSV. Please refresh the page and try again")
        return render(request, "fragments/messages.html")

    subscriber_csv.confirmed = True
    subscriber_csv.save()

    summary = None
    if subscriber_csv.is_ready_to_process:
        try:
            summary = process_subscriber_csv(subscriber_csv.pk)
            messages.success(request, "Subscriber import complete.")
        except Exception as error:
            messages.error(request, f"Subscriber import failed: {_safe_planka_error(error)}")

    return render(request, "backend/process_csv_success.html", {"summary": summary})


@login_required
def backend_go(request):
    """
    Role-aware destination chooser. Shown after login for staff/editorial users.
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

    searches_7d = recent_human.filter(event_type=AnalyticsEvent.EventType.SEARCH).count()

    # ── Issue workflow progress ───────────────────────────────────
    workflow_steps = []
    if current_issue:
        has_articles = PubmedBatchArticle.objects.filter(batch__issue=current_issue, selected=True).exists()
        has_reviewers = IssueContributor.objects.filter(issue=current_issue).exists()
        has_reviews = current_issue.reviews.filter(active=True).exists()
        is_published = current_issue.active
        issue_newsletter = Newsletter.objects.filter(issue=current_issue).first()
        newsletter_sent = issue_newsletter.is_sent if issue_newsletter else False

        workflow_steps = [
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


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
def subscriber_list(request):
    query = (request.GET.get("q") or "").strip()
    subscribed_filter = (request.GET.get("subscribed") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    view_mode = request.GET.get("view", "users")  # "users" or "subscribers"

    User = get_user_model()

    if view_mode == "subscribers":
        # Legacy subscriber-only view
        subscribers = Subscriber.objects.select_related("from_csv", "user").order_by("-modified", "-pk")
        if query:
            subscribers = subscribers.filter(Q(email__icontains=query) | Q(from_csv__name__icontains=query))
        if subscribed_filter in {"true", "false"}:
            subscribers = subscribers.filter(subscribed=(subscribed_filter == "true"))
        context = {
            "subscribers": subscribers[:300],
            "subscriber_filters": {
                "q": query,
                "subscribed": subscribed_filter,
                "view": view_mode,
                "role": role_filter,
            },
            "subscriber_total": subscribers.count(),
            "view_mode": view_mode,
        }
    else:
        # Users view: show User accounts with linked Subscriber data
        users = (
            User.objects.select_related("subscriber")
            .prefetch_related("user_permissions")
            .order_by("-last_login", "-date_joined")
        )
        if query:
            users = users.filter(Q(email__icontains=query) | Q(name__icontains=query))
        if subscribed_filter == "true":
            users = users.filter(subscriber__subscribed=True)
        elif subscribed_filter == "false":
            users = users.filter(Q(subscriber__isnull=True) | Q(subscriber__subscribed=False))
        if role_filter == "staff":
            users = users.filter(is_staff=True)
        elif role_filter == "can_recommend":
            from django.contrib.auth.models import Permission

            perm = Permission.objects.filter(content_type__app_label="submissions", codename="can_recommend").first()
            if perm:
                users = users.filter(user_permissions=perm)
        elif role_filter == "locked":
            users = users.filter(is_active=False)

        user_list = list(users[:300])
        # Pre-compute permission flags from prefetched permissions
        for u in user_list:
            perms = {p.codename for p in u.user_permissions.all()}
            u.has_perm_can_recommend = "can_recommend" in perms
            u.has_perm_chief_editor = "chief_editor" in perms

        context = {
            "user_list": user_list,
            "subscriber_filters": {
                "q": query,
                "subscribed": subscribed_filter,
                "view": view_mode,
                "role": role_filter,
            },
            "user_total": users.count(),
            "view_mode": view_mode,
        }

    if request.headers.get("HX-Request") == "true":
        return render(request, "backend/_subscriber_list_results.html", context)

    return render(request, "backend/subscriber_list.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
@require_POST
def user_toggle_recommend(request, user_id):
    """Toggle the can_recommend permission for a user."""
    from django.contrib.auth.models import Permission as DjangoPerm

    User = get_user_model()
    target_user = get_object_or_404(User, pk=user_id)
    perm = DjangoPerm.objects.get(content_type__app_label="submissions", codename="can_recommend")
    if target_user.has_perm("submissions.can_recommend"):
        target_user.user_permissions.remove(perm)
        messages.success(request, f"Removed recommend permission from {target_user.email}")
    else:
        target_user.user_permissions.add(perm)
        messages.success(request, f"Granted recommend permission to {target_user.email}")
    return redirect(reverse("backend:subscriber_list") + "?view=users")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def user_toggle_chief_editor(request, user_id):
    """Promote/demote a user to/from chief editor with full permission bundle."""
    from django.contrib.auth.models import Permission as DjangoPerm

    User = get_user_model()
    target_user = get_object_or_404(User, pk=user_id)
    if target_user == request.user:
        messages.error(request, "You cannot modify your own chief editor status.")
        return redirect(reverse("backend:subscriber_list") + "?view=users")

    is_chief = target_user.has_perm("submissions.chief_editor")

    if is_chief:
        # Demote: remove only chief_editor
        perm = DjangoPerm.objects.get(content_type__app_label="submissions", codename="chief_editor")
        target_user.user_permissions.remove(perm)
        messages.success(request, f"Removed chief editor role from {target_user.email}")
    else:
        # Promote: grant full bundle
        perm_specs = [
            ("submissions", "chief_editor"),
            ("submissions", "manage_issue_builder"),
            ("submissions", "regional_coordinator"),
            ("submissions", "can_recommend"),
            ("backend", "manage_subscriber_csv"),
            ("backend", "send_newsletters"),
            ("backend", "view_newsletter_stats"),
            ("backend", "view_site_analytics"),
        ]
        for app_label, codename in perm_specs:
            try:
                perm = DjangoPerm.objects.get(content_type__app_label=app_label, codename=codename)
                target_user.user_permissions.add(perm)
            except DjangoPerm.DoesNotExist:
                pass
        if not target_user.is_staff:
            target_user.is_staff = True
            target_user.save(update_fields=["is_staff"])
        messages.success(request, f"Promoted {target_user.email} to chief editor")

    return redirect(reverse("backend:subscriber_list") + "?view=users")


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
@require_POST
def user_toggle_active(request, user_id):
    """Toggle is_active (lock/unlock) for a user."""
    User = get_user_model()
    target_user = get_object_or_404(User, pk=user_id)
    if target_user == request.user:
        messages.error(request, "You cannot lock your own account.")
        return redirect(reverse("backend:subscriber_list") + "?view=users")
    target_user.is_active = not target_user.is_active
    target_user.save(update_fields=["is_active"])
    status = "unlocked" if target_user.is_active else "locked"
    messages.success(request, f"{target_user.email} has been {status}.")
    return redirect(reverse("backend:subscriber_list") + "?view=users")


def _get_pubmed_integration_credential():
    return PubmedIntegrationCredential.get_solo()


def _get_backend_preference():
    return BackendPreference.get_solo()


def _get_inbox_from_email():
    preference = _get_backend_preference()
    if preference:
        return preference.get_inbox_from_email()
    return BackendPreference().get_inbox_from_email()


def _build_backend_settings_context(request, *, inbox_settings_form=None, frontend_banner_form=None):
    from oauth2_provider.models import Application as OAuthApplication

    pubmed_credential = _get_pubmed_integration_credential()
    planka_credential = _get_planka_integration_credential()
    planka_client_id = os.getenv("OIDC_CLIENT_ID", "planka-local")
    planka_client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
    planka_oidc_app = OAuthApplication.objects.filter(client_id=planka_client_id).first()
    backend_preference = _get_backend_preference() or BackendPreference(singleton=1)

    if inbox_settings_form is None:
        inbox_settings_form = BackendPreferenceInboxSettingsForm(instance=backend_preference)
    if frontend_banner_form is None:
        frontend_banner_form = BackendPreferenceFrontendBannerForm(instance=backend_preference)

    planka_connected = False
    planka_connection_user = None
    planka_connection_error = None
    chief_editor_planka_user = None
    if planka_credential and planka_credential.get_api_key():
        try:
            client = PlankaClient(api_key=planka_credential.get_api_key(), access_token="")
            planka_connection_user = client.get_current_user()
            planka_connected = True
            if planka_credential.last_error:
                planka_credential.last_error = ""
                planka_credential.save(update_fields=["last_error", "modified"])
            chief_editor_planka_user = client.find_user_by_email(request.user.email)
        except PlankaAPIError as exc:
            planka_connection_error = _safe_planka_error(exc)
            if planka_credential.last_error != planka_connection_error:
                planka_credential.last_error = planka_connection_error
                planka_credential.save(update_fields=["last_error", "modified"])

    chief_editor_invites = ChiefEditorInvite.objects.order_by("-created")[:10]

    return {
        "now": timezone.now(),
        "pubmed_credential": pubmed_credential,
        "pubmed_api_key_form": PubmedApiKeyForm(),
        "planka_credential": planka_credential,
        "planka_oidc_app": planka_oidc_app,
        "planka_oidc_client_secret_configured": bool(planka_client_secret.strip()),
        "planka_connected": planka_connected,
        "planka_connection_user": planka_connection_user,
        "planka_connection_error": planka_connection_error,
        "chief_editor_planka_user": chief_editor_planka_user,
        "chief_editor_invites": chief_editor_invites,
        "inbox_settings_form": inbox_settings_form,
        "inbox_settings_preview": inbox_settings_form.get_preview_value(),
        "frontend_banner_form": frontend_banner_form,
    }


PAEDIATRIC_MESH_TERMS = {
    "Pediatrics",
    "Infant",
    "Infant, Newborn",
    "Child",
    "Child, Preschool",
    "Adolescent",
}
PAEDIATRIC_TEXT_TERMS = {
    "pediatric",
    "paediatric",
    "child",
    "children",
    "infant",
    "newborn",
    "neonat",
    "adolescent",
}
HUMANS_MESH_TERM = "Humans"
REVIEW_PUBLICATION_TYPES = {"Review", "Systematic Review", "Meta-Analysis"}
TRIAL_PUBLICATION_TYPES = {"Clinical Trial", "Randomized Controlled Trial"}

PAIN_TEXT_TERMS = {
    "pain",
    "analgesia",
    "analgesic",
    "opioid",
    "nocicept",
    "regional anaesthesia",
    "regional anesthesia",
}
PAIN_MESH_TERMS = {"Pain", "Pain Management", "Analgesia"}

ICU_TEXT_TERMS = {"intensive care", "critical care", "icu", "ventilat", "sepsis"}
ICU_MESH_TERMS = {"Critical Care", "Intensive Care Units", "Respiration, Artificial", "Sepsis"}

CARDIAC_TEXT_TERMS = {
    "cardiac anaesthesia",
    "cardiac anesthesia",
    "cardiothoracic",
    "cardiac surgery",
    "cardiopulmonary bypass",
    "heart surgery",
}
CARDIAC_MESH_TERMS = {"Anesthesia, Cardiovascular", "Cardiac Surgical Procedures", "Cardiopulmonary Bypass"}

NEONATAL_TEXT_TERMS = {"neonat", "newborn", "preterm", "premature"}
NEONATAL_MESH_TERMS = {"Infant, Newborn", "Premature Birth", "Infant, Premature"}


def _param_enabled(params, key, default=False):
    raw = params.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _import_pubmed_batch(batch, watched_journals):
    populate_pubmed_batch_from_cache(batch, watched_journals)


def _build_article_intake_queryset(batch, params):
    query = (params.get("q") or "").strip()
    watched_journal_id = (params.get("journal") or "").strip()
    selected = (params.get("filter_selected") or params.get("selected") or "").strip().lower()
    paediatric_only = _param_enabled(params, "paediatric_only", default=False)
    humans_only = _param_enabled(params, "humans_only", default=False)
    review_only = _param_enabled(params, "review_only", default=False)
    trial_only = _param_enabled(params, "trial_only", default=False)
    pain_only = _param_enabled(params, "pain_only", default=False)
    icu_only = _param_enabled(params, "icu_only", default=False)
    cardiac_only = _param_enabled(params, "cardiac_only", default=False)
    neonatal_only = _param_enabled(params, "neonatal_only", default=False)

    rows = (
        batch.batch_articles.select_related("article", "watched_journal", "issue")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            )
        )
        .order_by("-article__publication_date", "article__title")
    )

    if query:
        rows = rows.filter(
            Q(article__title__icontains=query)
            | Q(article__abstract__icontains=query)
            | Q(article__doi__icontains=query)
            | Q(article__pmid__icontains=query)
        )
    if watched_journal_id.isdigit():
        rows = rows.filter(watched_journal_id=int(watched_journal_id))
    if selected in {"true", "false"}:
        rows = rows.filter(is_selected=(selected == "true"))

    filtered_rows = list(rows)
    if paediatric_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_topic(row.article, mesh_terms=PAEDIATRIC_MESH_TERMS, text_terms=PAEDIATRIC_TEXT_TERMS)
        ]
    if humans_only:
        filtered_rows = [
            row for row in filtered_rows if _article_matches_metadata(row.article, "mesh_terms", {HUMANS_MESH_TERM})
        ]
    if review_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_metadata(row.article, "publication_types", REVIEW_PUBLICATION_TYPES)
        ]
    if trial_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_metadata(row.article, "publication_types", TRIAL_PUBLICATION_TYPES)
        ]
    if pain_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_topic(row.article, mesh_terms=PAIN_MESH_TERMS, text_terms=PAIN_TEXT_TERMS)
        ]
    if icu_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_topic(row.article, mesh_terms=ICU_MESH_TERMS, text_terms=ICU_TEXT_TERMS)
        ]
    if cardiac_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_topic(row.article, mesh_terms=CARDIAC_MESH_TERMS, text_terms=CARDIAC_TEXT_TERMS)
        ]
    if neonatal_only:
        filtered_rows = [
            row
            for row in filtered_rows
            if _article_matches_topic(row.article, mesh_terms=NEONATAL_MESH_TERMS, text_terms=NEONATAL_TEXT_TERMS)
        ]

    return (
        filtered_rows,
        query,
        watched_journal_id,
        selected,
        paediatric_only,
        humans_only,
        review_only,
        trial_only,
        pain_only,
        icu_only,
        cardiac_only,
        neonatal_only,
    )


def _article_intake_results_context(batch, params):
    watched_options = list(batch.watched_journals.order_by("name"))

    (
        rows,
        query,
        watched_journal_id,
        selected,
        paediatric_only,
        humans_only,
        review_only,
        trial_only,
        pain_only,
        icu_only,
        cardiac_only,
        neonatal_only,
    ) = _build_article_intake_queryset(batch, params)

    tab_params = params.copy()
    tab_params["journal"] = ""
    tab_rows, *_ = _build_article_intake_queryset(batch, tab_params)
    journal_counts = {}
    for row in tab_rows:
        journal_counts[row.watched_journal_id] = journal_counts.get(row.watched_journal_id, 0) + 1

    watched_journal_tabs = [
        {
            "journal": watched,
            "count": journal_counts.get(watched.pk, 0),
        }
        for watched in watched_options
    ]

    paginator = Paginator(rows, 25)
    page_obj = paginator.get_page(params.get("page") or 1)
    visible_rows = list(page_obj.object_list)
    all_visible_selected = bool(visible_rows) and all(row.is_selected for row in visible_rows)
    staged_rows = list(
        batch.batch_articles.select_related("article", "watched_journal", "issue")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            )
        )
        .filter(is_selected=True)
        .order_by("-modified")[:200]
    )
    return {
        "batch": batch,
        "page_obj": page_obj,
        "result_rows": visible_rows,
        "all_visible_selected": all_visible_selected,
        "all_journals_count": len(tab_rows),
        "staged_rows": staged_rows,
        "result_total": len(rows),
        "selected_total": batch.batch_articles.filter(is_selected=True).count(),
        "pushed_total": batch.batch_articles.exclude(planka_card_id="").count(),
        "filter_query": query,
        "filter_journal": watched_journal_id,
        "filter_selected": selected,
        "filter_paediatric_only": paediatric_only,
        "filter_humans_only": humans_only,
        "filter_review_only": review_only,
        "filter_trial_only": trial_only,
        "filter_pain_only": pain_only,
        "filter_icu_only": icu_only,
        "filter_cardiac_only": cardiac_only,
        "filter_neonatal_only": neonatal_only,
        "watched_journal_options": watched_options,
        "watched_journal_tabs": watched_journal_tabs,
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
    context = _article_intake_results_context(batch, params)
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


def _get_issue_planka_candidates_list(batch, *, require_candidates_list=True):
    issue = batch.issue
    if not issue:
        return None, None, "Assign this batch to an issue before pushing to Planka."

    binding = PlankaIssueBinding.objects.filter(issue=issue).first()
    if not binding:
        return issue, None, "No Planka project linked to this issue. Set up Planka first."

    candidates_list_id = binding.get_list_id("candidates")
    if require_candidates_list and not candidates_list_id:
        return issue, binding, "Candidates list is not configured for this Planka board."

    return issue, binding, ""


def _build_pubmed_article_citation(article):
    parts = []
    if article.source_journal_name:
        parts.append(str(article.source_journal_name).strip())
    if article.publication_date:
        parts.append(article.publication_date.strftime("%Y-%m-%d"))
    if article.doi:
        parts.append(f"DOI: {article.doi}")
    if article.pmid:
        parts.append(f"PMID: {article.pmid}")
    return " · ".join([part for part in parts if part])


def _decode_planka_escaped_text(value):
    text = str(value or "")
    if not text:
        return ""

    def _replace_unicode(match):
        try:
            return chr(int(match.group(1), 16))
        except Exception:
            return match.group(0)

    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", _replace_unicode, text)
    decoded = decoded.replace("\\n", "\n").replace("\\r", "\r")
    # Planka's markdown renderer escapes angle brackets and other special chars
    decoded = decoded.replace("\\<", "<").replace("\\>", ">")
    return decoded


def _parse_planka_card_metadata(description_text):
    description = _decode_planka_escaped_text(description_text)
    if PLANKA_REVIEW_SEPARATOR_MARKER in description:
        header_text, _ = description.split(PLANKA_REVIEW_SEPARATOR_MARKER, 1)
    else:
        header_text = description

    metadata = {
        "journal_name": "",
        "article_url": "",
        "article_year": "",
        "article_abstract": "",
        "article_citation": "",
    }

    journal_match = re.search(r"(?mi)^Journal:\s*(.+?)\s*$", header_text)
    if journal_match:
        metadata["journal_name"] = journal_match.group(1).strip()

    url_match = re.search(r"(?mi)^Article URL:\s*(.+?)\s*$", header_text)
    if url_match:
        metadata["article_url"] = url_match.group(1).strip().strip("<>")

    publication_match = re.search(r"(?mi)^Publication date:\s*([0-9]{4})(?:-[0-9]{2}(?:-[0-9]{2})?)?\s*$", header_text)
    if publication_match:
        metadata["article_year"] = publication_match.group(1).strip()

    abstract_match = re.search(r"(?ms)^Abstract\s*\n[-]{2,}\s*\n(?P<body>.+)$", header_text.strip())
    if abstract_match:
        metadata["article_abstract"] = abstract_match.group("body").strip()

    return metadata


def _get_board_label_map(*, client, board_id):
    _, included = client.get_board(board_id)
    labels = included.get("labels", []) or []
    return {
        str(label.get("name") or "").strip().lower(): str(label.get("id") or "").strip()
        for label in labels
        if str(label.get("name") or "").strip() and str(label.get("id") or "").strip()
    }


def _get_board_labels(*, client, board_id):
    _, included = client.get_board(board_id)
    return included.get("labels", []) or []


def _get_board_list_type_map(*, client, board_id):
    _, included = client.get_board(board_id)
    lists = included.get("lists", []) or []
    mapping = {}
    for item in lists:
        list_id = str(item.get("id") or "").strip()
        if not list_id:
            continue
        mapping[list_id] = str(item.get("type") or "").strip().lower()
    return mapping


def _get_next_board_label_position(*, client, board_id):
    labels = _get_board_labels(client=client, board_id=board_id)
    positions = []
    for label in labels:
        try:
            positions.append(int(float(label.get("position") or 0)))
        except (TypeError, ValueError):
            continue

    if not positions:
        return 65536
    return max(positions) + 65536


def _pick_journal_label_color(journal_name):
    normalized = str(journal_name or "").strip().lower()
    if not normalized:
        return "berry-red"

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(PLANKA_JOURNAL_LABEL_COLORS)
    return PLANKA_JOURNAL_LABEL_COLORS[index]


def _pick_non_used_label_color(*, client, board_id, preferred_color):
    labels = _get_board_labels(client=client, board_id=board_id)
    used = {str(label.get("color") or "").strip() for label in labels if str(label.get("color") or "").strip()}

    preferred = str(preferred_color or "").strip()
    if preferred and preferred in PLANKA_JOURNAL_LABEL_COLORS and preferred not in used:
        return preferred

    for color in PLANKA_JOURNAL_LABEL_COLORS:
        if color not in used:
            return color

    if preferred and preferred in PLANKA_JOURNAL_LABEL_COLORS:
        return preferred
    return "berry-red"


def _ensure_existing_label_color(*, client, board_id, label_id, preferred_color):
    label_id = str(label_id or "").strip()
    if not label_id:
        return

    labels = _get_board_labels(client=client, board_id=board_id)
    current = next((label for label in labels if str(label.get("id") or "").strip() == label_id), None)
    if not current:
        return

    current_color = str(current.get("color") or "").strip()
    if not current_color:
        return

    # Keep existing color when it is unique on the board.
    color_counts = {}
    for label in labels:
        color = str(label.get("color") or "").strip()
        if not color:
            continue
        color_counts[color] = color_counts.get(color, 0) + 1

    if color_counts.get(current_color, 0) <= 1:
        return

    target_color = _pick_non_used_label_color(client=client, board_id=board_id, preferred_color=preferred_color)
    if not target_color or target_color == current_color:
        return

    client.update_label(label_id, color=target_color)


def _get_or_create_board_label_id(*, client, board_id, label_name, label_cache):
    normalized_name = str(label_name or "").strip()
    if not normalized_name:
        return ""

    cache_key = normalized_name.lower()
    cached = label_cache.get(cache_key)
    if cached:
        try:
            _ensure_existing_label_color(
                client=client,
                board_id=board_id,
                label_id=cached,
                preferred_color=_pick_journal_label_color(normalized_name),
            )
        except PlankaAPIError:
            pass
        return cached

    try:
        preferred_color = _pick_journal_label_color(normalized_name)
        label = client.create_label(
            board_id=board_id,
            name=normalized_name,
            color=_pick_non_used_label_color(
                client=client,
                board_id=board_id,
                preferred_color=preferred_color,
            ),
            position=_get_next_board_label_position(client=client, board_id=board_id),
        )
        label_id = str(label.get("id") or "").strip()
        if label_id:
            label_cache[cache_key] = label_id
        return label_id
    except PlankaAPIError:
        refreshed_map = _get_board_label_map(client=client, board_id=board_id)
        label_cache.update(refreshed_map)
        label_id = label_cache.get(cache_key, "")
        if label_id:
            try:
                _ensure_existing_label_color(
                    client=client,
                    board_id=board_id,
                    label_id=label_id,
                    preferred_color=_pick_journal_label_color(normalized_name),
                )
            except PlankaAPIError:
                pass
        return label_id


def _attach_journal_label_to_card(*, client, binding, card_id, row, label_cache):
    journal_name = str(row.article.source_journal_name or "").strip()
    if not journal_name:
        return

    label_id = _get_or_create_board_label_id(
        client=client,
        board_id=binding.board_id,
        label_name=journal_name,
        label_cache=label_cache,
    )
    if not label_id:
        return

    try:
        client.add_label_to_card(card_id=card_id, label_id=label_id)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error).lower()
        if "e_conflict" in safe_error or "already" in safe_error:
            return
        raise


def _normalize_planka_review_body(text):
    body, _ = _extract_planka_review_body(text)
    return body


def _extract_planka_review_body(text):
    def _canonicalize(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    raw_text = _decode_planka_escaped_text(text)
    if PLANKA_REVIEW_SEPARATOR_MARKER in raw_text:
        _, review_text = raw_text.split(PLANKA_REVIEW_SEPARATOR_MARKER, 1)
        review_text = review_text.strip()
        return review_text, True

    body = raw_text.strip()
    if not body:
        return "", False

    if _canonicalize(body) == _canonicalize(PLANKA_LEGACY_REVIEW_DESCRIPTION_TEMPLATE):
        return "", False

    return body, False


def _refresh_binding_lists_from_board(*, client, binding):
    _, included = client.get_board(binding.board_id)
    lists = included.get("lists", []) or []

    key_by_label = {label.lower(): key for key, label in PLANKA_LIST_LABELS.items()}
    existing = dict(binding.lists or {})
    changed = False
    for list_item in lists:
        list_id = str(list_item.get("id") or "").strip()
        list_name = str(list_item.get("name") or "").strip().lower()
        key = key_by_label.get(list_name)
        if not key or not list_id:
            continue
        if existing.get(key) != list_id:
            existing[key] = list_id
            changed = True

    if changed:
        binding.lists = existing
        binding.save(update_fields=["lists", "modified"])


def _ensure_planka_board_mappings(*, client, binding):
    # Refresh list mappings from Planka so stale local ids do not break pushes.
    _refresh_binding_lists_from_board(client=client, binding=binding)


def _build_pubmed_planka_card(row):
    article = row.article
    title = (article.title or "").strip() or f"PMID {article.pmid}"
    lines = []

    if article.source_journal_name:
        lines.append(f"Journal: {article.source_journal_name}")

    if article.publication_date:
        lines.append(f"Publication date: {article.publication_date:%Y-%m-%d}")
    elif article.publication_month:
        lines.append(f"Publication date: {article.publication_month:%Y-%m}")

    if article.article_url:
        lines.append(f"Article URL: {article.article_url}")
    elif article.pubmed_url:
        lines.append(f"Article URL: {article.pubmed_url}")

    abstract = (article.abstract or "").strip()
    if abstract:
        lines.append("")
        lines.append("Abstract")
        lines.append("--------")
        lines.append(abstract)

    lines.append("")
    lines.extend(PLANKA_REVIEW_INSTRUCTIONS.splitlines())
    lines.append("")
    lines.append("---")
    lines.append(PLANKA_REVIEW_SEPARATOR_MARKER)
    lines.append("")
    lines.extend(PLANKA_REVIEW_SCAFFOLD.splitlines())

    return title, "\n".join(lines).strip()


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

            if _param_enabled(request.POST, "async", default=False):
                _queue_batch_task(
                    batch,
                    action="fetch",
                    note="Queued intake batch build from cached journal articles.",
                    task_callable=run_pubmed_batch_import_task,
                    task_args=[batch.pk],
                )
            else:
                try:
                    _import_pubmed_batch(batch, watched_journals)
                    messages.success(request, f"Loaded {batch.result_count} cached article(s) into the intake batch.")
                except PubmedAPIError as error:
                    messages.error(request, f"Could not build batch from cache: {_safe_planka_error(error)}")

                    return redirect(f"{reverse('backend:article_intake')}?issue={selected_issue.pk}&batch={batch.pk}")

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
        context.update(_article_intake_results_context(batch, request.GET))

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
    context = _article_intake_results_context(batch, request.GET)
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
    from django.template.loader import render_to_string

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

        payload = payloads[0]
        doi = (payload.get("doi") or "").strip().lower() or None

        article = None
        if doi:
            article = PubmedArticle.objects.filter(doi=doi).first()
        if not article:
            article = PubmedArticle.objects.filter(pmid=pmid).first()

        if not article:
            article = PubmedArticle.objects.create(
                pmid=pmid,
                doi=doi,
                title=payload.get("title") or "",
                abstract=payload.get("abstract") or "",
                source_journal_name=payload.get("source_journal_name") or "",
                publication_date=payload.get("publication_date"),
                publication_month=payload.get("publication_month"),
                article_url=payload.get("article_url") or "",
                pubmed_url=payload.get("pubmed_url") or "",
                metadata_json=payload.get("metadata_json") or {},
            )
        else:
            _fill_missing_article_metadata(article, payload)

        PubmedBatchArticle.objects.create(batch=batch, article=article, issue=batch.issue, is_selected=True)
        messages.success(request, f"\u201c{article.title}\u201d added to staging.")
        new_selected = True

    batch.result_count = batch.batch_articles.count()
    batch.selected_count = batch.batch_articles.filter(is_selected=True).count()
    batch.save(update_fields=["result_count", "selected_count", "modified"])

    highlighted_pmid = pmid if new_selected else None

    # Build results table HTML
    results_context = _article_intake_results_context(batch, request.POST)
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
                        planka_client = _build_planka_client()
                        candidates_list_id = binding.get_list_id("candidates")
                        list_type_map = _get_board_list_type_map(client=planka_client, board_id=binding.board_id)
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
def article_intake_refresh_batch(request, batch_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)

    watched_journals = list(batch.watched_journals.filter(active=True))
    if not watched_journals:
        messages.error(request, "No active watched journals on this batch.")
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST)
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    if _param_enabled(request.POST, "async", default=False):
        _queue_batch_task(
            batch,
            action="refresh",
            note="Queued rebuild from cached journal articles.",
            task_callable=run_pubmed_batch_import_task,
            task_args=[batch.pk],
        )
        if request.headers.get("HX-Request") == "true":
            return _render_article_intake_results_response(request, batch, request.POST)
        return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")

    try:
        _import_pubmed_batch(batch, watched_journals)
        messages.success(request, f"Batch rebuilt from cache. {batch.result_count} article(s) now in this batch.")
    except PubmedAPIError as error:
        messages.error(request, f"Could not rebuild batch from cache: {_safe_planka_error(error)}")

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST)

    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def article_intake_refresh_cache(request, batch_id):
    """Trigger a PubMed API cache refresh for the batch's journals and date range."""
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    batch = get_object_or_404(PubmedImportBatch, pk=batch_id)

    from_month = batch.from_month.isoformat() if batch.from_month else None
    to_month = batch.to_month.isoformat() if batch.to_month else None

    if not from_month or not to_month:
        messages.error(request, "Batch has no date range set.")
    else:
        refresh_pubmed_journal_cache_task.delay(from_month=from_month, to_month=to_month)
        messages.success(
            request,
            f"PubMed cache refresh queued for {batch.from_month:%b %Y} - {batch.to_month:%b %Y}. "
            "Use 'Rebuild current batch from cache' once complete to load new articles.",
        )

    if request.headers.get("HX-Request") == "true":
        return _render_article_intake_results_response(request, batch, request.POST)

    return redirect(f"{reverse('backend:article_intake')}?batch={batch.pk}")


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
        client = _build_planka_client()
        _ensure_planka_board_mappings(client=client, binding=binding)
        label_cache = _get_board_label_map(client=client, board_id=binding.board_id)
        list_type_map = _get_board_list_type_map(client=client, board_id=binding.board_id)
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
        client = _build_planka_client()
        _ensure_planka_board_mappings(client=client, binding=binding)
        list_type_map = _get_board_list_type_map(client=client, board_id=binding.board_id)
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
        context.update(_article_intake_results_context(batch, request.GET))

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
            user_states__recommended_at__isnull=False,
            recommendation_hidden=show_hidden,
        )
        .annotate(
            recommendation_count=Count(
                "user_states",
                filter=Q(user_states__recommended_at__isnull=False),
                distinct=True,
            ),
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

    recommended_articles = list(qs.order_by("-recommendation_count", "-publication_date", "title")[:100])

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


def _copy_issue_image_to_newsletter(newsletter, issue):
    """Copy issue.image into newsletter.header_image, triggering greyscale processing."""
    import logging as _logging
    import os as _os

    from django.core.files.base import ContentFile as _ContentFile

    if not (issue and issue.image and issue.image.name):
        return False
    try:
        issue.image.open("rb")
        content = issue.image.read()
        issue.image.close()
        filename = _os.path.basename(issue.image.name)
        newsletter.header_image_processed = False
        newsletter.save(update_fields=["header_image_processed"])
        newsletter.header_image.save(filename, _ContentFile(content), save=True)
        return True
    except (OSError, ValueError) as exc:
        _logging.getLogger(__name__).warning("Could not copy issue image to newsletter: %s", exc)
        return False


def _newsletter_ready_to_send(newsletter):
    return bool(
        newsletter.content_heading and newsletter.content and newsletter.header_image and newsletter.header_image.name
    )


def _newsletter_stats(newsletter):
    from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen

    opens_qs = NewsletterOpen.objects.filter(newsletter=newsletter)
    clicks_qs = NewsletterClick.objects.filter(newsletter=newsletter)
    total_opens = opens_qs.values("subscriber").count()
    opens = opens_qs.values("subscriber").distinct().count()
    total_clicks = clicks_qs.values("subscriber").count()
    clicks = clicks_qs.values("subscriber").distinct().count()
    human_opens_qs = opens_qs.filter(automated=False)
    human_clicks_qs = clicks_qs.filter(automated=False)
    total_human_opens = human_opens_qs.values("subscriber").count()
    human_opens = human_opens_qs.values("subscriber").distinct().count()
    total_human_clicks = human_clicks_qs.values("subscriber").count()
    human_clicks = human_clicks_qs.values("subscriber").distinct().count()
    automated_opens = max(total_opens - total_human_opens, 0)
    automated_clicks = max(total_clicks - total_human_clicks, 0)
    return {
        "emails_sent": newsletter.emails_sent,
        "opens": opens,
        "total_opens": total_opens,
        "clicks": clicks,
        "total_clicks": total_clicks,
        "human_opens": human_opens,
        "total_human_opens": total_human_opens,
        "human_clicks": human_clicks,
        "total_human_clicks": total_human_clicks,
        "automated_opens": automated_opens,
        "automated_clicks": automated_clicks,
        "automated_open_share": f"{round(automated_opens / total_opens * 100)}%" if total_opens else "0%",
        "automated_click_share": f"{round(automated_clicks / total_clicks * 100)}%" if total_clicks else "0%",
        "open_rate": f"{round(opens / newsletter.emails_sent * 100)}%" if newsletter.emails_sent else "—",
        "human_open_rate": f"{round(human_opens / newsletter.emails_sent * 100)}%" if newsletter.emails_sent else "—",
        "click_through_rate": f"{round(clicks / opens * 100)}%" if opens else "—",
        "human_click_through_rate": f"{round(human_clicks / human_opens * 100)}%" if human_opens else "—",
    }


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def newsletter_release_list(request):
    selected_issue = _resolve_and_persist_issue(request)
    newsletter = None
    edit_form = None

    if selected_issue:
        newsletter = Newsletter.objects.filter(issue=selected_issue).order_by("-pk").first()
        if not newsletter:
            newsletter = Newsletter.objects.create(
                issue=selected_issue,
                subject=f"Journal Watch \u2014 {selected_issue.name}",
                content=selected_issue.body or "",
            )

        if request.method == "POST":
            edit_form = NewsletterEditForm(request.POST, request.FILES, instance=newsletter)
            if edit_form.is_valid():
                new_image_uploaded = bool(request.FILES.get("header_image"))
                use_issue_image = edit_form.cleaned_data.get("use_issue_image")
                if new_image_uploaded:
                    edit_form.instance.header_image_processed = False
                newsletter = edit_form.save(commit=False)
                newsletter.ready_to_send = _newsletter_ready_to_send(newsletter)
                newsletter.save()
                if use_issue_image and not new_image_uploaded:
                    _copy_issue_image_to_newsletter(newsletter, selected_issue)
                    newsletter.refresh_from_db()
                    newsletter.ready_to_send = _newsletter_ready_to_send(newsletter)
                    newsletter.save(update_fields=["ready_to_send"])
                messages.success(request, "Newsletter saved.")
                return redirect(f"{reverse('backend:newsletter_release_list')}?issue={selected_issue.pk}")
        else:
            edit_form = NewsletterEditForm(instance=newsletter)

    newsletter_stats = _newsletter_stats(newsletter) if (newsletter and newsletter.is_sent) else None

    newsletter_header_image_url = None
    if newsletter and newsletter.header_image and newsletter.header_image.name:
        try:
            url = newsletter.header_image.url
            # In local dev, skip images whose files don't exist to avoid 404s
            if settings.DEBUG and not newsletter.header_image.storage.exists(newsletter.header_image.name):
                pass
            else:
                newsletter_header_image_url = url
        except Exception:
            pass

    context = {
        "selected_issue": selected_issue,
        "newsletter": newsletter,
        "newsletter_stats": newsletter_stats,
        "edit_form": edit_form,
        "test_form": NewsletterTestSendForm(),
        "issue_for_preview": selected_issue
        if (selected_issue and selected_issue.image and selected_issue.image.name)
        else None,
        "newsletter_header_image_url": newsletter_header_image_url,
    }
    return render(request, "backend/newsletter_release_list.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def create_newsletter(request):
    if request.method == "POST":
        form = NewsletterCreateForm(request.POST, request.FILES)
        if form.is_valid():
            newsletter = form.save()
            # If no custom image uploaded but "use issue image" is checked, copy issue.image
            if form.cleaned_data.get("use_issue_image") and not newsletter.header_image:
                _copy_issue_image_to_newsletter(newsletter, newsletter.issue)
            messages.success(request, f"Newsletter {newsletter} created.")
            return redirect(reverse("backend:newsletter_release_list") + f"?issue={newsletter.issue_id}")
    else:
        selected_issue = _resolve_and_persist_issue(request, fallback_latest=False)
        initial = (
            {"issue": selected_issue, "use_issue_image": bool(selected_issue and selected_issue.image)}
            if selected_issue
            else {}
        )
        form = NewsletterCreateForm(initial=initial)

    # Pass pre-selected issue image for the preview (only meaningful on initial GET)
    issue_for_preview = None
    if request.method == "GET":
        if selected_issue and selected_issue.image:
            issue_for_preview = selected_issue
    context = {"form": form, "issue_for_preview": issue_for_preview}
    return render(request, "backend/create_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def final_newsletter(request, send_token):
    # Provides last check before sending
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    context = {
        "send_token": send_token,
        "newsletter": newsletter,
        "test_form": NewsletterTestSendForm(),
    }
    return render(request, "backend/final_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_test_newsletter(request, send_token):
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    if not newsletter:
        messages.error(request, "This token is no longer valid. Please refresh and try again.")
        return redirect("backend:dashboard")

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    form = NewsletterTestSendForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        send_newsletter_test_email.apply_async((newsletter.pk, email), countdown=1)
        messages.success(request, f"Test newsletter queued for {email}")
    else:
        messages.error(request, "Please enter a valid test email address.")

    return redirect(reverse("backend:final_newsletter", kwargs={"send_token": send_token}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def enable_newsletter_resend(request, send_token):
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    if not newsletter:
        messages.error(request, "This token is no longer valid. Please refresh and try again.")
        return redirect("backend:dashboard")

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    if not newsletter.is_sent:
        messages.error(request, "This newsletter has not been sent yet.")
    else:
        newsletter.resend_enabled = True
        newsletter.save(update_fields=["resend_enabled", "send_token"])
        messages.warning(request, "Resend enabled for this newsletter. It can now be sent once more.")

    return redirect(reverse("backend:final_newsletter", kwargs={"send_token": newsletter.send_token}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_final_newsletter(request, send_token):
    try:
        newsletter = Newsletter.objects.get(send_token=send_token)
        if newsletter.is_ready_to_send():
            # Celery task also checks is_ready_to_send
            send_newsletter.apply_async((newsletter.pk,), {"test_email": False}, countdown=1)
            messages.success(request, f"Newsletter {newsletter} queued for sending")
        else:
            messages.error(request, f"Newsletter {newsletter} not sent: not ready")

    except Newsletter.DoesNotExist:
        messages.error(request, "This token is no longer valid. Please re-send a test newsletter")
        newsletter = {}

    return render(request, "backend/send_final_newsletter.html", {"newsletter": newsletter})


@login_required
@permission_required("backend.view_newsletter_stats", raise_exception=True)  # Prevents login loop
def newsletter_stats_detail(request, pk):
    newsletter = get_object_or_404(Newsletter, pk=pk)

    opens_qs = NewsletterOpen.objects.filter(newsletter=newsletter)
    clicks_qs = NewsletterClick.objects.filter(newsletter=newsletter)

    subscriber_opens = opens_qs.values("subscriber")
    total_opens = subscriber_opens.count()
    opens = subscriber_opens.distinct().count()

    subscriber_clicks = clicks_qs.values("subscriber")
    total_clicks = subscriber_clicks.count()
    clicks = subscriber_clicks.distinct().count()

    human_subscriber_opens = opens_qs.filter(automated=False).values("subscriber")
    total_human_opens = human_subscriber_opens.count()
    human_opens = human_subscriber_opens.distinct().count()

    human_subscriber_clicks = clicks_qs.filter(automated=False).values("subscriber")
    total_human_clicks = human_subscriber_clicks.count()
    human_clicks = human_subscriber_clicks.distinct().count()

    automated_opens = max(total_opens - total_human_opens, 0)
    automated_clicks = max(total_clicks - total_human_clicks, 0)
    automated_open_share = f"{str(round(automated_opens/total_opens*100))}%" if total_opens else "0%"
    automated_click_share = f"{str(round(automated_clicks/total_clicks*100))}%" if total_clicks else "0%"

    open_rate = f"{str(round(opens/newsletter.emails_sent*100))}%" if newsletter.emails_sent else "0"
    click_through_rate = f"{str(round(clicks/opens*100))}%" if opens else "0"
    human_open_rate = f"{str(round(human_opens/newsletter.emails_sent*100))}%" if newsletter.emails_sent else "0"
    human_click_through_rate = f"{str(round(human_clicks/human_opens*100))}%" if human_opens else "0"

    context = {
        "newsletter": newsletter,
        "newsletters_sent": newsletter.emails_sent,
        "total_opens": total_opens,
        "opens": opens,
        "total_clicks": total_clicks,
        "clicks": clicks,
        "open_rate": open_rate,
        "click_through_rate": click_through_rate,
        "total_human_opens": total_human_opens,
        "human_opens": human_opens,
        "total_human_clicks": total_human_clicks,
        "human_clicks": human_clicks,
        "human_open_rate": human_open_rate,
        "human_click_through_rate": human_click_through_rate,
        "automated_opens": automated_opens,
        "automated_clicks": automated_clicks,
        "automated_open_share": automated_open_share,
        "automated_click_share": automated_click_share,
    }

    template = "backend/newsletter_stats_detail.html"

    return render(request, template, context)


def _parse_iso_date(value):
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _safe_percentage(numerator, denominator):
    if not denominator:
        return "0%"
    return f"{round((numerator / denominator) * 100)}%"


def _build_rate_row(*, label, opens=0, engaged=0, shares=0, full_text=0, searches=0, result_clicks=0, score=0):
    actions = shares + full_text
    return {
        "label": label,
        "opens": opens,
        "engaged": engaged,
        "shares": shares,
        "full_text": full_text,
        "actions": actions,
        "searches": searches,
        "result_clicks": result_clicks,
        "engaged_rate": _safe_percentage(engaged, opens),
        "share_rate": _safe_percentage(shares, opens),
        "full_text_ctr": _safe_percentage(full_text, opens),
        "action_rate": _safe_percentage(actions, engaged),
        "search_click_rate": _safe_percentage(result_clicks, searches),
        "score": score,
    }


@login_required
@permission_required("backend.view_newsletter_stats", raise_exception=True)
def site_analytics(request):
    today = timezone.localdate()
    default_start = today - datetime.timedelta(days=90)
    start_date = _parse_iso_date(request.GET.get("start")) or default_start
    end_date = _parse_iso_date(request.GET.get("end")) or today
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    start_ts = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
    end_ts = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))

    human_events = AnalyticsEvent.objects.filter(timestamp__gte=start_ts, timestamp__lte=end_ts, automated=False)
    human_event_count = human_events.count()
    review_content_type = ContentType.objects.get_for_model(Review)
    review_events = human_events.filter(content_type=review_content_type)

    share_event_types = [
        AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
        AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE,
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    ]
    social_share_event_types = {
        AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY,
        AnalyticsEvent.EventType.REVIEW_SHARE_X,
        AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK,
    }

    review_summary_rows = list(
        review_events.values("object_id")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged_views=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            avg_dwell_ms=Avg("duration_ms", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            full_text_clicks=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
            copy_link_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK)),
            email_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL)),
            native_shares=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE)),
            social_shares=Count("id", filter=Q(event_type__in=social_share_event_types)),
            total_shares=Count("id", filter=Q(event_type__in=share_event_types)),
        )
        .order_by("-opens", "-engaged_views", "-full_text_clicks")
    )

    review_ids = [row["object_id"] for row in review_summary_rows if row["object_id"]]
    reviews_by_id = {
        review.id: review
        for review in Review.objects.filter(id__in=review_ids)
        .select_related("article__journal", "author")
        .prefetch_related("article__tags")
    }

    top_reviews = []
    journal_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})
    tag_totals = defaultdict(lambda: {"opens": 0, "engaged": 0, "shares": 0, "full_text": 0, "score": 0})

    for row in review_summary_rows:
        review = reviews_by_id.get(row["object_id"])
        if not review:
            continue

        engagement_score = (
            row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
        )

        summary = {
            "review": review,
            "opens": row["opens"],
            "engaged_views": row["engaged_views"],
            "avg_dwell_seconds": round((row["avg_dwell_ms"] or 0) / 1000, 1),
            "full_text_clicks": row["full_text_clicks"],
            "copy_link_shares": row["copy_link_shares"],
            "email_shares": row["email_shares"],
            "native_shares": row["native_shares"],
            "social_shares": row["social_shares"],
            "total_shares": row["total_shares"],
            "engagement_score": engagement_score,
            "engaged_rate": _safe_percentage(row["engaged_views"], row["opens"]),
            "share_rate": _safe_percentage(row["total_shares"], row["opens"]),
            "full_text_ctr": _safe_percentage(row["full_text_clicks"], row["opens"]),
            "action_rate": _safe_percentage(row["total_shares"] + row["full_text_clicks"], row["engaged_views"]),
            "is_featured": review.is_featured,
        }
        top_reviews.append(summary)

        journal = review.article.journal.name if review.article.journal else "Unknown journal"
        journal_totals[journal]["opens"] += row["opens"]
        journal_totals[journal]["engaged"] += row["engaged_views"]
        journal_totals[journal]["shares"] += row["total_shares"]
        journal_totals[journal]["full_text"] += row["full_text_clicks"]
        journal_totals[journal]["score"] += engagement_score

        for tag in review.article.tags.all():
            label = str(tag)
            tag_totals[label]["opens"] += row["opens"]
            tag_totals[label]["engaged"] += row["engaged_views"]
            tag_totals[label]["shares"] += row["total_shares"]
            tag_totals[label]["full_text"] += row["full_text_clicks"]
            tag_totals[label]["score"] += engagement_score

    top_reviews = sorted(top_reviews, key=lambda item: item["engagement_score"], reverse=True)[:12]
    top_journals = sorted(
        ({"label": label, **totals} for label, totals in journal_totals.items()),
        key=lambda item: item["score"],
        reverse=True,
    )[:10]
    top_tags = sorted(
        ({"label": label, **totals} for label, totals in tag_totals.items()),
        key=lambda item: item["score"],
        reverse=True,
    )[:12]

    total_review_opens = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_OPEN).count()
    total_engaged_views = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).count()
    total_full_text_clicks = review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK).count()
    total_shares = review_events.filter(event_type__in=share_event_types).count()
    average_engaged_ms = (
        review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED).aggregate(avg=Avg("duration_ms"))[
            "avg"
        ]
        or 0
    )

    share_counts = {
        "Copy link": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK).count(),
        "Email": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL).count(),
        "Native share": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_NATIVE).count(),
        "Bluesky": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_BLUESKY).count(),
        "X": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_X).count(),
        "Facebook": review_events.filter(event_type=AnalyticsEvent.EventType.REVIEW_SHARE_FACEBOOK).count(),
    }
    share_breakdown = [{"label": label, "count": count} for label, count in share_counts.items() if count]

    confidence_rows = list(
        human_events.values("human_confidence")
        .annotate(
            events=Count("id"),
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            shares=Count("id", filter=Q(event_type__in=share_event_types)),
            full_text=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
        )
        .order_by("-events")
    )
    confidence_labels = {
        AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN: "Known subscriber human",
        AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN: "Probable human",
        AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED: "Suspected automated",
    }
    confidence_breakdown = [
        {
            "label": confidence_labels.get(row["human_confidence"], row["human_confidence"] or "Unknown"),
            "events": row["events"],
            "opens": row["opens"],
            "engaged": row["engaged"],
            "shares": row["shares"],
            "full_text": row["full_text"],
            "share_of_events": _safe_percentage(row["events"], human_event_count),
        }
        for row in confidence_rows
    ]
    known_subscriber_events = next(
        (
            row["events"]
            for row in confidence_rows
            if row["human_confidence"] == AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN
        ),
        0,
    )
    probable_human_events = next(
        (
            row["events"]
            for row in confidence_rows
            if row["human_confidence"] == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN
        ),
        0,
    )

    source_rows = list(
        review_events.values("source")
        .annotate(
            opens=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)),
            engaged=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED)),
            shares=Count("id", filter=Q(event_type__in=share_event_types)),
            full_text=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK)),
        )
        .order_by("-opens", "-engaged")
    )
    source_labels = {
        "home-review-modal": "Homepage modal",
        "review_modal": "Review modal",
        "review_detail": "Review detail page",
        "issue_detail": "Issue detail page",
        "author-review-modal": "Author modal",
        "tag-review-modal": "Tag modal",
        "search_results": "Search results",
        "search_page": "Search page",
    }
    source_breakdown = [
        _build_rate_row(
            label=source_labels.get(row["source"], row["source"] or "Unknown"),
            opens=row["opens"],
            engaged=row["engaged"],
            shares=row["shares"],
            full_text=row["full_text"],
            score=(row["opens"] + (row["engaged"] * 3) + (row["full_text"] * 4) + (row["shares"] * 5)),
        )
        for row in source_rows
    ]

    featured_ids = {review.id for review in reviews_by_id.values() if review.is_featured}
    featured_review_rows = [row for row in review_summary_rows if row["object_id"] in featured_ids]
    standard_review_rows = [row for row in review_summary_rows if row["object_id"] not in featured_ids]

    def _summarize_review_group(label, rows):
        opens = sum(row["opens"] for row in rows)
        engaged = sum(row["engaged_views"] for row in rows)
        shares = sum(row["total_shares"] for row in rows)
        full_text = sum(row["full_text_clicks"] for row in rows)
        score = sum(
            row["opens"] + (row["engaged_views"] * 3) + (row["full_text_clicks"] * 4) + (row["total_shares"] * 5)
            for row in rows
        )
        return _build_rate_row(
            label=label,
            opens=opens,
            engaged=engaged,
            shares=shares,
            full_text=full_text,
            score=score,
        )

    review_type_breakdown = [
        _summarize_review_group("Featured reviews", featured_review_rows),
        _summarize_review_group("Standard reviews", standard_review_rows),
    ]

    search_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH)
    search_click_events = human_events.filter(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK)
    search_query_counter = Counter()
    search_result_counter = Counter()
    zero_result_counter = Counter()
    for metadata in search_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        search_query_counter[label] += 1
        if not (metadata or {}).get("result_count"):
            zero_result_counter[label] += 1
    for metadata in search_click_events.values_list("metadata", flat=True):
        query = (metadata or {}).get("query") or ""
        label = query.strip() or "[browse]"
        search_result_counter[label] += 1

    search_insights = []
    for label, count in search_query_counter.most_common(10):
        click_count = search_result_counter.get(label, 0)
        search_insights.append(
            {
                "label": label,
                "searches": count,
                "result_clicks": click_count,
                "click_through_rate": _safe_percentage(click_count, count),
                "zero_results": zero_result_counter.get(label, 0),
            }
        )

    content_opportunities = []
    for item in sorted(top_reviews, key=lambda row: (-row["opens"], row["engaged_views"]))[:6]:
        if item["opens"] >= 1 and item["engaged_views"] < item["opens"]:
            content_opportunities.append(
                {
                    "review": item["review"],
                    "signal": "High opens, weaker engagement",
                    "detail": f"{item['engaged_rate']} engaged, {item['share_rate']} shared",
                }
            )
    for item in sorted(top_reviews, key=lambda row: (-row["engagement_score"], -row["total_shares"]))[:6]:
        if item["engagement_score"] and item["total_shares"]:
            content_opportunities.append(
                {
                    "review": item["review"],
                    "signal": "Strong engagement and sharing",
                    "detail": f"Score {item['engagement_score']}, {item['total_shares']} shares",
                }
            )
    content_opportunities = content_opportunities[:8]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_review_opens": total_review_opens,
        "total_engaged_views": total_engaged_views,
        "engaged_rate": _safe_percentage(total_engaged_views, total_review_opens),
        "total_full_text_clicks": total_full_text_clicks,
        "full_text_ctr": _safe_percentage(total_full_text_clicks, total_review_opens),
        "total_shares": total_shares,
        "share_rate": _safe_percentage(total_shares, total_review_opens),
        "average_dwell_seconds": round(average_engaged_ms / 1000, 1),
        "search_count": search_events.count(),
        "search_result_click_count": search_click_events.count(),
        "search_click_rate": _safe_percentage(search_click_events.count(), search_events.count()),
        "share_breakdown": share_breakdown,
        "confidence_breakdown": confidence_breakdown,
        "known_subscriber_events": known_subscriber_events,
        "known_subscriber_share": _safe_percentage(known_subscriber_events, human_event_count),
        "probable_human_events": probable_human_events,
        "probable_human_share": _safe_percentage(probable_human_events, human_event_count),
        "source_breakdown": source_breakdown,
        "review_type_breakdown": review_type_breakdown,
        "top_reviews": top_reviews,
        "top_journals": top_journals,
        "top_tags": top_tags,
        "search_insights": search_insights,
        "content_opportunities": content_opportunities,
    }
    return render(request, "backend/site_analytics.html", context)


@login_required
def analytics_redirect(request):
    return HttpResponseRedirect(reverse("backend:analytics_overview"))


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

    Priority: ?issue= param in GET/POST > session > most-recently-modified issue.
    """
    issue_id = (request.GET.get("issue") or request.POST.get("issue") or "").strip()
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

    secret = (getattr(settings, "PLANKA_WEBHOOK_SECRET", "") or "").strip() or None
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

        client = _build_planka_client()

        planka_user = client.find_user_by_email(contributor.email)

        desired_name = (contributor.name or "").strip() or contributor.email
        if not planka_user:
            planka_user = client.create_user(contributor.email, desired_name)
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
    client = _build_planka_client()
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


def _extract_publish_cards(binding):
    return _filter_board_cards_by_scope(_extract_board_cards(binding), "publish")


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


def _issue_invite_ttl_days():
    return int(getattr(settings, "ISSUE_CONTRIBUTOR_INVITE_TTL_DAYS", 180))


def _build_issue_invite_url(request, raw_token):
    return request.build_absolute_uri(reverse("issue_invite_accept", kwargs={"token": raw_token}))


def _create_issue_contributor_invite(contributor, created_by):
    now = timezone.now()
    expires_at = now + datetime.timedelta(days=_issue_invite_ttl_days())
    raw_token = IssueContributorInvite.generate_raw_token()
    token_hash = IssueContributorInvite.hash_token(raw_token)

    IssueContributorInvite.objects.filter(
        contributor=contributor,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)

    invite = IssueContributorInvite.objects.create(
        contributor=contributor,
        token_hash=token_hash,
        expires_at=expires_at,
        created_by=created_by,
    )
    return invite, raw_token


def _send_issue_invite_email(request, invite, raw_token):
    contributor = invite.contributor
    issue = contributor.issue
    accept_url = _build_issue_invite_url(request, raw_token)
    context = {
        "issue": issue,
        "contributor": contributor,
        "accept_url": accept_url,
        "expires_at": invite.expires_at,
        "docs_url": request.build_absolute_uri(reverse("backend:docs")),
    }

    subject = f"Invitation to contribute to {issue.name}"
    text_body = render_to_string("backend/email/issue_contributor_invite.txt", context)
    html_body = render_to_string("backend/email/issue_contributor_invite.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=None,
        to=[contributor.email],
        reply_to=[settings.CONTACT_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")
    message.metadata = {"type": "issue_contributor_invite", "issue_id": issue.pk}
    message.tags = ["issue-contributor-invite"]
    message.send()

    invite.sent_at = timezone.now()
    invite.save(update_fields=["sent_at", "modified"])


def _send_issue_welcome_email(request, contributor):
    issue = contributor.issue
    planka_url = getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "")
    context = {
        "issue": issue,
        "contributor": contributor,
        "planka_url": planka_url,
        "docs_url": request.build_absolute_uri(reverse("backend:docs")),
    }
    subject = f"Thank you for agreeing to review for {issue.name}"
    text_body = render_to_string("backend/email/issue_contributor_welcome.txt", context)
    html_body = render_to_string("backend/email/issue_contributor_welcome.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=None,
        to=[contributor.email],
        reply_to=[settings.CONTACT_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")
    message.metadata = {"type": "issue_contributor_welcome", "issue_id": issue.pk}
    message.tags = ["issue-contributor-welcome"]
    message.send()


def _render_issue_contributors_panel(request, issue, invite_form=None, role="reviewer"):
    context = _issue_builder_base_context(issue=issue)
    if invite_form is not None:
        context["issue_contributor_invite_form"] = invite_form
    if role == IssueContributor.Role.COORDINATOR:
        return render(request, "backend/issue_builder/_issue_coordinators_panel.html", context)
    return render(request, "backend/issue_builder/_issue_contributors_panel.html", context)


def _panel_role_from_request(request, contributor=None):
    """Determine which panel to re-render: check POST data, then fall back to contributor's role."""
    role = request.POST.get("panel_role", "")
    if role in (IssueContributor.Role.COORDINATOR, IssueContributor.Role.REVIEWER):
        return role
    if contributor is not None:
        return contributor.role
    return IssueContributor.Role.REVIEWER


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
    issue_qs = Issue.objects.prefetch_related("reviews__article", "reviews__author").order_by("-modified")
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

    if issue:
        binding = PlankaIssueBinding.objects.filter(issue=issue).first()
        context["planka_binding"] = binding
        context["planka_setup_form"] = PlankaProjectSetupForm(initial={"project_name": issue.name})
        if binding and binding.background_asset_id:
            context["planka_background_form"].fields["background_asset"].initial = binding.background_asset_id
        if binding:
            context["planka_project_name_form"].fields["project_name"].initial = binding.project_name

        context["review_form"] = review_form or IssueBuilderReviewForm(issue=issue)
        context["review_form_action"] = form_action or reverse(
            "backend:add_issue_review",
            kwargs={"issue_id": issue.pk},
        )
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


def _build_article_mesh_context(article):
    """Return a list of dicts for each MeSH term on the article, with mapping status."""
    mesh_terms = (article.metadata_json or {}).get("mesh_terms", [])
    if not mesh_terms:
        return []
    mapped = dict(MeshTagMapping.objects.filter(mesh_term__in=mesh_terms).values_list("mesh_term", "tag__text"))
    return [{"term": t, "tag": mapped.get(t)} for t in sorted(mesh_terms)]


def _render_issue_panel(request, issue, review_form=None, form_action=None, is_edit=False):
    context = _issue_builder_base_context(
        issue=issue,
        review_form=review_form,
        form_action=form_action,
        is_edit=is_edit,
    )
    return render(request, "backend/issue_builder/_issue_reviews_panel.html", context)


def _get_issue_review_readiness(issue):
    if not issue:
        return []
    reviews = issue.reviews.select_related("author", "article").all()
    result = []
    for review in reviews:
        indicators = [
            {"label": "Body", "ok": bool((review.body or "").strip()), "required": True},
            {"label": "Author", "ok": review.author is not None, "required": True},
            {"label": "Article title", "ok": bool(review.article.get_title()), "required": True},
        ]
        if review.is_featured:
            indicators.append({"label": "Feature image", "ok": bool(review.feature_image), "required": True})
        is_ready = all(i["ok"] for i in indicators if i["required"])
        result.append({"review": review, "indicators": indicators, "is_ready": is_ready})
    return result


def _validate_issue_publish(issue):
    errors = []
    max_featured = int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2))
    reviews = issue.reviews.select_related("article", "author").all()

    if not issue.name or not issue.body:
        errors.append("Issue requires a title and body before publishing.")

    if not reviews.exists():
        errors.append("Add at least one review before publishing.")

    featured_count = reviews.filter(is_featured=True).count()
    if featured_count > max_featured:
        errors.append(f"Only {max_featured} featured reviews are allowed.")

    for review in reviews:
        if not review.article_id:
            errors.append(f"Review {review.pk} is missing an article.")
        if not review.author_id:
            errors.append(f"Review {review.pk} is missing an author.")
        if not review.body:
            errors.append(f"Review {review.pk} is missing body content.")

    return errors


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_builder(request):
    if _is_coordinator_only(request.user):
        # Coordinators do not access the setup step; redirect to the reviewers page.
        issue_id = (request.GET.get("issue") or request.POST.get("issue") or "").strip()
        target = reverse("backend:issue_reviewers")
        if issue_id.isdigit():
            target += f"?issue={issue_id}"
        return redirect(target)

    issue = _resolve_and_persist_issue(request)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_reviewers(request):
    issue = _resolve_and_persist_issue(request)
    _check_coordinator_issue_access(request, issue)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_reviewers.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_reviews_edit(request):
    issue = _resolve_and_persist_issue(request)
    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_reviews_edit.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_publish(request):
    from spanza_journal_watch.layout.models import Homepage

    issue = _resolve_and_persist_issue(request)
    current_homepage = Homepage.get_current_homepage()
    context = _issue_builder_base_context(issue=issue)
    context["current_homepage"] = current_homepage
    context["review_readiness"] = _get_issue_review_readiness(issue)
    return render(request, "backend/issue_builder/issue_publish.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_set_homepage(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    from spanza_journal_watch.layout.models import Homepage

    issue = _resolve_and_persist_issue(request)
    if not issue:
        messages.error(request, "No issue selected.")
        return redirect(reverse("backend:issue_publish"))
    Homepage.objects.update(publication_ready=False)
    homepage, _ = Homepage.objects.get_or_create(issue=issue)
    homepage.publication_ready = True
    homepage.save()
    Homepage.publish_homepage(homepage)
    messages.success(request, f'"{issue.name}" is now set as the homepage.')
    return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def toggle_review_active(request, review_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    from spanza_journal_watch.layout.models import Homepage

    review = get_object_or_404(Review, pk=review_id)
    issue = review.issues.first()

    review.active = not review.active
    review.save(update_fields=["active"])

    if review.active:
        if not review.article.active:
            review.article.active = True
            review.article.save(update_fields=["active", "modified"])
        if issue and not issue.active:
            issue.active = True
            issue.save(update_fields=["active", "modified"])
    else:
        if issue:
            any_active = issue.reviews.filter(active=True).exists()
            if not any_active:
                issue.active = False
                issue.save(update_fields=["active", "modified"])

    current_homepage = Homepage.get_current_homepage()
    context = _issue_builder_base_context(issue=issue)
    context["current_homepage"] = current_homepage
    context["review_readiness"] = _get_issue_review_readiness(issue)
    return render(request, "backend/issue_builder/_publish_reviews_panel.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def issue_planka_import(request):
    issue = _resolve_and_persist_issue(request)

    context = _issue_builder_base_context(issue=issue)
    binding = context.get("planka_binding")
    if issue and binding:
        card_scope = (request.GET.get("scope") or "publish").strip().lower()
        if card_scope not in {"publish", "all"}:
            card_scope = "publish"
        try:
            board_cards = _extract_board_cards(binding)
            scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
            context["planka_publish_cards"] = scoped_cards
            context["planka_scope_counts"] = _build_planka_scope_counts(board_cards)
            context["planka_card_scope"] = card_scope
            context["planka_publish_summary"] = _build_planka_publish_summary(scoped_cards)
            if request.GET.get("refresh") == "1":
                summary = context["planka_publish_summary"]
                context["planka_panel_status"] = (
                    f"Refresh complete. {summary['total']} cards loaded in this view "
                    f"({summary['valid']} ready, {summary['missing']} with missing fields, "
                    f"{summary['already_imported']} already imported/protected)."
                )
                context["planka_panel_status_level"] = "success"
        except PlankaAPIError as error:
            safe_error = _safe_planka_error(error)
            context["planka_publish_cards"] = []
            context["planka_publish_summary"] = _build_planka_publish_summary([])
            context["planka_scope_counts"] = {"publish": 0, "all": 0}
            context["planka_card_scope"] = card_scope
            if _is_planka_connection_error(error):
                context["planka_panel_status"] = "Not connected to Planka. Retrying in background…"
                context["planka_disconnected"] = True
            elif _is_planka_board_not_found_error(error):
                context["planka_panel_status"] = (
                    "Linked Reviews board was not found in Planka. You can recreate the board for this issue."
                )
                context["planka_board_missing"] = True
            else:
                context["planka_panel_status"] = f"Could not refresh Planka cards: {safe_error}"
            context["planka_panel_status_level"] = "danger"

    if issue:
        staged_total = PubmedBatchArticle.objects.filter(issue=issue, is_selected=True).count()
        latest_batch = PubmedImportBatch.objects.filter(issue=issue).order_by("-created", "-pk").first()
        context["intake_staged_total"] = staged_total
        context["intake_batch_id"] = latest_batch.pk if latest_batch else ""

    return render(request, "backend/issue_builder/planka_import.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def fetch_monitoring(request):
    """Dashboard showing NIH/PubMed fetch history and status."""
    logs = FetchLog.objects.all()[:50]
    recent_success = FetchLog.objects.filter(status=FetchLog.STATUS_SUCCESS).order_by("-finished_at").first()
    recent_error = FetchLog.objects.filter(status=FetchLog.STATUS_ERROR).order_by("-finished_at").first()

    # Stats for last 7 days
    seven_days_ago = timezone.now() - datetime.timedelta(days=7)
    recent_logs = FetchLog.objects.filter(started_at__gte=seven_days_ago)
    stats = {
        "total": recent_logs.count(),
        "success": recent_logs.filter(status=FetchLog.STATUS_SUCCESS).count(),
        "error": recent_logs.filter(status=FetchLog.STATUS_ERROR).count(),
        "avg_duration": recent_logs.filter(duration_seconds__isnull=False).aggregate(avg=Avg("duration_seconds"))[
            "avg"
        ],
    }

    # Get next scheduled run from django-celery-beat
    next_scheduled = None
    try:
        from django_celery_beat.models import PeriodicTask

        task = PeriodicTask.objects.filter(
            task="spanza_journal_watch.backend.tasks.refresh_pubmed_journal_cache_task",
            enabled=True,
        ).first()
        if task and task.last_run_at:
            next_scheduled = task.last_run_at + datetime.timedelta(hours=12)
    except Exception:
        pass

    # MeSH refresh status
    mesh_refresh_stats = None
    try:
        from django_celery_beat.models import PeriodicTask as PT

        mesh_task = PT.objects.filter(
            task="spanza_journal_watch.backend.tasks.refresh_mesh_terms_task",
            enabled=True,
        ).first()
        if mesh_task:
            last_mesh_log = (
                FetchLog.objects.filter(details__type="mesh_refresh")
                .exclude(status=FetchLog.STATUS_RUNNING)
                .order_by("-finished_at")
                .first()
            )
            # Count those actually without MeSH (can't filter JSON easily, use the log)
            mesh_refresh_stats = {
                "task_enabled": True,
                "last_run": mesh_task.last_run_at,
                "last_log": last_mesh_log,
            }
    except Exception:
        pass

    context = {
        "fetch_logs": logs,
        "recent_success": recent_success,
        "recent_error": recent_error,
        "stats": stats,
        "next_scheduled": next_scheduled,
        "mesh_refresh_stats": mesh_refresh_stats,
    }
    return render(request, "backend/fetch_monitoring.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def trigger_mesh_refresh(request):
    from .tasks import refresh_mesh_terms_task

    refresh_mesh_terms_task.delay()
    messages.success(request, "MeSH refresh task queued. Check fetch monitoring for progress.")
    return redirect("backend:fetch_monitoring")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def backend_settings(request):
    context = _build_backend_settings_context(request)
    return render(request, "backend/settings.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def save_inbox_sender_settings(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    preference = _get_backend_preference() or BackendPreference(singleton=1)
    form = BackendPreferenceInboxSettingsForm(request.POST, instance=preference)
    if form.is_valid():
        form.save()
        messages.success(request, "Inbox sender settings saved.")
        return redirect(reverse("backend:backend_settings"))

    context = _build_backend_settings_context(request, inbox_settings_form=form)
    response = render(request, "backend/settings.html", context)
    response.status_code = 400
    return response


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def save_frontend_banner_settings(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    preference = _get_backend_preference() or BackendPreference(singleton=1)
    form = BackendPreferenceFrontendBannerForm(request.POST, instance=preference)
    if form.is_valid():
        form.save()
        messages.success(request, "Frontend banner settings saved.")
        return redirect(reverse("backend:backend_settings"))

    context = _build_backend_settings_context(request, frontend_banner_form=form)
    response = render(request, "backend/settings.html", context)
    response.status_code = 400
    return response


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def send_chief_editor_invite(request):
    """Send an email invitation to become chief editor."""
    email = (request.POST.get("email") or "").strip()
    name = (request.POST.get("name") or "").strip()

    if not email:
        messages.error(request, "Email address is required.")
        return redirect(reverse("backend:backend_settings"))

    # Revoke any active invites for this email
    now = timezone.now()
    ChiefEditorInvite.objects.filter(
        email__iexact=email,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)

    raw_token = ChiefEditorInvite.generate_raw_token()
    token_hash = ChiefEditorInvite.hash_token(raw_token)
    expires_at = now + datetime.timedelta(days=180)

    invite = ChiefEditorInvite.objects.create(
        email=email,
        name=name,
        token_hash=token_hash,
        expires_at=expires_at,
        created_by=request.user,
    )

    accept_url = request.build_absolute_uri(reverse("chief_editor_invite_accept", kwargs={"token": raw_token}))
    context = {
        "invite": invite,
        "invited_by": request.user,
        "accept_url": accept_url,
    }

    subject = "Invitation to become a Chief Editor on SPANZA Journal Watch"
    text_body = render_to_string("backend/email/chief_editor_invite.txt", context)
    html_body = render_to_string("backend/email/chief_editor_invite.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=None,
        to=[email],
        reply_to=[settings.CONTACT_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")
    message.metadata = {"type": "chief_editor_invite"}
    message.tags = ["chief-editor-invite"]
    message.send()

    invite.sent_at = timezone.now()
    invite.save(update_fields=["sent_at", "modified"])

    messages.success(request, f"Chief editor invitation sent to {email}.")
    return redirect(reverse("backend:backend_settings"))


def chief_editor_invite_accept(request, token):
    """Accept a chief editor invitation."""
    token_hash = ChiefEditorInvite.hash_token(token)
    invite = ChiefEditorInvite.objects.filter(token_hash=token_hash).first()

    context = {
        "invite": invite,
        "status": "invalid",
        "status_message": "This invite link is invalid.",
    }
    template = "backend/invites/accept_chief_editor_invite.html"

    if not invite:
        return render(request, template, context)

    now = timezone.now()

    if invite.expires_at <= now:
        context["status"] = "expired"
        return render(request, template, context)

    if not request.user.is_authenticated:
        User = get_user_model()
        account_exists = User.objects.filter(email__iexact=invite.email).exists()
        invite_path = request.get_full_path()
        request.session["_pending_invite_token"] = token
        request.session["pending_invite_email"] = invite.email
        context["status"] = "unauthenticated"
        context["invited_email"] = invite.email
        context["account_exists"] = account_exists
        context["login_url"] = f"{reverse('account_login')}?next={invite_path}"
        context["signup_url"] = f"{reverse('account_signup')}?next={invite_path}"
        return render(request, template, context)

    expected_email = (invite.email or "").strip().lower()
    user_email = (request.user.email or "").strip().lower()

    if expected_email != user_email:
        from django.contrib.auth import logout as auth_logout

        auth_logout(request)
        messages.info(
            request,
            f"You've been signed out. Please sign in or create an account"
            f" with {invite.email} to accept this invite.",
        )
        return redirect(request.get_full_path())

    if invite.consumed_at and invite.accepted_by == request.user:
        context["status"] = "accepted"
        context["status_message"] = "Invite already accepted."
        return render(request, template, context)

    with transaction.atomic():
        invite.consumed_at = now
        invite.accepted_by = request.user
        invite.save(update_fields=["consumed_at", "accepted_by", "modified"])

        # Update user name from invite if not set
        invite_name = (invite.name or "").strip()
        if invite_name and not (getattr(request.user, "name", "") or "").strip():
            request.user.name = invite_name
            request.user.save(update_fields=["name"])

        # Grant chief editor permission bundle
        from django.contrib.auth.models import Permission as DjangoPerm

        perm_specs = [
            ("submissions", "chief_editor"),
            ("submissions", "manage_issue_builder"),
            ("submissions", "regional_coordinator"),
            ("submissions", "can_recommend"),
            ("backend", "manage_subscriber_csv"),
            ("backend", "send_newsletters"),
            ("backend", "view_newsletter_stats"),
            ("backend", "view_site_analytics"),
        ]
        for app_label, codename in perm_specs:
            try:
                perm = DjangoPerm.objects.get(content_type__app_label=app_label, codename=codename)
                request.user.user_permissions.add(perm)
            except DjangoPerm.DoesNotExist:
                logger.error("Permission %s.%s not found during chief editor invite acceptance.", app_label, codename)

        if not request.user.is_staff:
            request.user.is_staff = True
            request.user.save(update_fields=["is_staff"])

        # Clear permission cache
        for attr in ("_perm_cache", "_user_perm_cache"):
            request.user.__dict__.pop(attr, None)

    # Mark email as verified
    from allauth.account.models import EmailAddress

    EmailAddress.objects.update_or_create(
        user=request.user,
        email=request.user.email,
        defaults={"verified": True, "primary": True},
    )
    request.session.pop("_pending_invite_token", None)
    request.session.pop("pending_invite_email", None)

    context["status"] = "accepted"
    return render(request, template, context)


def _run_management_command(command_name, **kwargs):
    """Run a management command, capture its stdout/stderr, return (success, output)."""
    import io as _io

    from django.core.management import call_command
    from django.core.management.base import CommandError

    buf = _io.StringIO()
    try:
        call_command(command_name, stdout=buf, stderr=buf, no_color=True, **kwargs)
        return True, buf.getvalue()
    except CommandError as exc:
        output = buf.getvalue()
        if output:
            return False, f"{output}\n{exc}"
        return False, str(exc)
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_run_setup_oidc(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["POST"])
    success, output = _run_management_command("setup_planka_oidc")
    return render(
        request,
        "backend/_setup_command_result.html",
        {
            "success": success,
            "output": output,
            "command": "setup_planka_oidc",
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_run_setup_api_key(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["POST"])
    success, output = _run_management_command("setup_planka_api_key")
    return render(
        request,
        "backend/_setup_command_result.html",
        {
            "success": success,
            "output": output,
            "command": "setup_planka_api_key",
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_promote_chief_editor(request):
    """Promote the requesting chief editor's Planka account to the admin role."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["POST"])
    try:
        client = _build_planka_client()
        planka_user = client.find_user_by_email(request.user.email)
        if not planka_user:
            return render(
                request,
                "backend/_setup_command_result.html",
                {
                    "success": False,
                    "output": (
                        f"No Planka account found for {request.user.email}.\n"
                        "Log into Planka via SSO first, then return here to promote your account."
                    ),
                    "command": "planka_promote_chief_editor",
                },
            )
        if planka_user.get("role") == "admin":
            return render(
                request,
                "backend/_setup_command_result.html",
                {
                    "success": True,
                    "output": f"Account {request.user.email} is already a Planka admin.",
                    "command": "planka_promote_chief_editor",
                },
            )
        client.set_user_role(str(planka_user["id"]), "admin")
        return render(
            request,
            "backend/_setup_command_result.html",
            {
                "success": True,
                "output": f"Promoted {request.user.email} to Planka admin.",
                "command": "planka_promote_chief_editor",
            },
        )
    except PlankaAPIError as exc:
        return render(
            request,
            "backend/_setup_command_result.html",
            {
                "success": False,
                "output": f"Planka API error: {_safe_planka_error(exc)}",
                "command": "planka_promote_chief_editor",
            },
        )


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

    return render(
        request,
        "backend/authors.html",
        {
            "authors": Author.objects.prefetch_related("health_services").order_by("name"),
            "form": form,
            "editing": author,
            "q": "",
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def save_issue_draft(request, issue_id=None):
    # Creating a new issue requires chief_editor; updating an existing one requires
    # only manage_issue_builder (already enforced by the decorator above).
    if issue_id is None and not request.user.has_perm("submissions.chief_editor"):
        raise PermissionDenied
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None
    _check_coordinator_issue_access(request, issue)
    form = IssueBuilderIssueForm(request.POST, request.FILES, instance=issue)

    if form.is_valid():
        issue = form.save(commit=False)
        if not issue.pk:
            issue.active = False
        issue.save()
        messages.success(request, "Issue draft saved.")
        return_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}"
        if request.headers.get("HX-Request") == "true":
            from django.http import HttpResponse as _HttpResponse

            response = _HttpResponse()
            response["HX-Redirect"] = return_url
            return response
        return redirect(return_url)

    context = _issue_builder_base_context(issue=issue)
    context["issue_form"] = form
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def new_review_form(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(issue=issue)
    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            "is_edit": False,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def add_issue_review(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue)

    if form.is_valid():
        form.save()
        messages.success(request, "Review added to issue draft.")
        return _render_issue_panel(request, issue)

    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            "is_edit": False,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def edit_issue_review_form(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(issue=issue, review=review)

    # Build MeSH term context for the article
    article = review.article
    mesh_context = _build_article_mesh_context(article)

    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse(
                "backend:update_issue_review",
                kwargs={"issue_id": issue.pk, "review_id": review.pk},
            ),
            "is_edit": True,
            "article_mesh_terms": mesh_context,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def update_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue, review=review)

    if form.is_valid():
        form.save()
        messages.success(request, "Review updated.")
        return _render_issue_panel(request, issue)

    return render(
        request,
        "backend/issue_builder/_issue_review_editor_page.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse(
                "backend:update_issue_review", kwargs={"issue_id": issue.pk, "review_id": review.pk}
            ),
            "is_edit": True,
        },
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def remove_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    issue.reviews.remove(review)
    messages.success(request, "Review removed from issue.")

    return _render_issue_panel(request, issue)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def contributor_author_lookup(request):
    """JSON: look up an existing Author by email, returning name and affiliations."""
    from django.http import JsonResponse

    email = (request.GET.get("email") or "").strip().lower()
    if not email:
        return JsonResponse({"found": False})
    author = Author.objects.prefetch_related("health_services").filter(email__iexact=email).first()
    if not author:
        return JsonResponse({"found": False})
    affiliations = [{"id": hs.pk, "name": hs.name} for hs in author.health_services.all()]
    return JsonResponse(
        {
            "found": True,
            "name": author.name or "",
            "affiliations": affiliations,
            "has_affiliations": bool(affiliations),
        }
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_add_contributor(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    _check_coordinator_issue_access(request, issue)

    # Collect rows: name_0/email_0, name_1/email_1, ...
    role = request.POST.get("role", "")
    rows = []
    i = 0
    while True:
        name = request.POST.get(f"name_{i}", "").strip()
        email = request.POST.get(f"email_{i}", "").strip()
        if not name and not email:
            break
        rows.append((i, name, email))
        i += 1

    panel_role = _panel_role_from_request(request)

    if not rows:
        messages.error(request, "Please provide at least one name and email.")
        return _render_issue_contributors_panel(request, issue, role=panel_role)

    for idx, name, email in rows:
        if not name or not email:
            messages.warning(request, f"Row {idx + 1}: name and email are both required — skipped.")
            continue

        affiliation_names = [n.strip() for n in request.POST.getlist(f"affiliation_{idx}") if n.strip()]

        contributor, created = IssueContributor.objects.get_or_create(
            issue=issue,
            email=email,
            defaults={
                "name": name,
                "role": role,
                "status": IssueContributor.Status.PENDING,
                "accepted_at": None,
                "revoked_at": None,
                "planka_sync_state": IssueContributor.PlankaSyncState.PENDING,
                "planka_last_error": "",
            },
        )

        if not created:
            contributor.name = name
            contributor.role = role
            if contributor.status not in (
                IssueContributor.Status.ACTIVE,
                IssueContributor.Status.INVITED,
            ):
                contributor.status = IssueContributor.Status.PENDING
            contributor.planka_sync_state = IssueContributor.PlankaSyncState.PENDING
            contributor.planka_last_error = ""
            contributor.save(
                update_fields=[
                    "name",
                    "role",
                    "status",
                    "planka_sync_state",
                    "planka_last_error",
                    "modified",
                ]
            )

        # Link to existing Author by email, or create one if affiliation is provided.
        if not contributor.author:
            existing_author = Author.objects.prefetch_related("health_services").filter(email__iexact=email).first()
            if existing_author:
                contributor.author = existing_author
                contributor.save(update_fields=["author", "modified"])
            elif affiliation_names:
                new_author = Author.objects.create(name=name, email=email)
                contributor.author = new_author
                contributor.save(update_fields=["author", "modified"])

        # Add any submitted affiliations to the Author (never removes existing ones).
        if affiliation_names and contributor.author:
            for affiliation_name in affiliation_names:
                hs, _ = HealthService.objects.get_or_create(name=affiliation_name)
                contributor.author.health_services.add(hs)

        planka_ok, planka_error = _sync_contributor_to_planka(contributor)
        if not planka_ok:
            messages.warning(request, f"Planka board access could not be set up for {email}: {planka_error}")

        if created:
            messages.success(request, f"Added {email}.")
        else:
            messages.success(request, f"Updated {email}.")

    return _render_issue_contributors_panel(request, issue, role=panel_role)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_send_contributor_invites(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    _check_coordinator_issue_access(request, issue)
    contributor_ids = request.POST.getlist("contributor_ids")
    panel_role = _panel_role_from_request(request)

    if not contributor_ids:
        messages.error(request, "No contributors selected.")
        return _render_issue_contributors_panel(request, issue, role=panel_role)

    contributors = IssueContributor.objects.filter(
        issue=issue,
        pk__in=contributor_ids,
    ).exclude(status=IssueContributor.Status.REVOKED)

    now = timezone.now()
    for contributor in contributors:
        contributor.status = IssueContributor.Status.INVITED
        contributor.invited_by = request.user
        contributor.invited_at = now
        contributor.save(update_fields=["status", "invited_by", "invited_at", "modified"])

        invite, raw_token = _create_issue_contributor_invite(contributor, request.user)
        try:
            _send_issue_invite_email(request, invite, raw_token)
            messages.success(request, f"Invite sent to {contributor.email}.")
        except Exception as error:
            messages.error(request, f"Could not send invite to {contributor.email}: {error}")

    return _render_issue_contributors_panel(request, issue, role=panel_role)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_resend_contributor_invite(request, issue_id, contributor_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    _check_coordinator_issue_access(request, issue)
    contributor = get_object_or_404(IssueContributor, pk=contributor_id, issue=issue)

    contributor.status = IssueContributor.Status.INVITED
    contributor.invited_by = request.user
    contributor.invited_at = timezone.now()
    contributor.revoked_at = None
    contributor.planka_sync_state = IssueContributor.PlankaSyncState.PENDING
    contributor.planka_last_error = ""
    contributor.save(
        update_fields=[
            "status",
            "invited_by",
            "invited_at",
            "revoked_at",
            "planka_sync_state",
            "planka_last_error",
            "modified",
        ]
    )

    planka_ok, planka_error = _sync_contributor_to_planka(contributor)
    if not planka_ok:
        messages.warning(request, f"Planka board access could not be set up: {planka_error}")

    invite, raw_token = _create_issue_contributor_invite(contributor, request.user)
    try:
        _send_issue_invite_email(request, invite, raw_token)
        messages.success(request, f"Invite resent to {contributor.email}.")
    except Exception as error:
        messages.error(request, f"Could not resend invite email: {error}")

    return _render_issue_contributors_panel(request, issue, role=contributor.role)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_sync_contributor_planka(request, issue_id, contributor_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    _check_coordinator_issue_access(request, issue)
    contributor = get_object_or_404(IssueContributor, pk=contributor_id, issue=issue)

    ok, error = _sync_contributor_to_planka(contributor)
    if ok:
        messages.success(request, f"Planka access synced for {contributor.email}.")
    else:
        messages.warning(request, f"Planka sync failed: {error}")

    return _render_issue_contributors_panel(request, issue, role=contributor.role)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_revoke_contributor(request, issue_id, contributor_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    _check_coordinator_issue_access(request, issue)
    contributor = get_object_or_404(IssueContributor, pk=contributor_id, issue=issue)
    now = timezone.now()

    if contributor.planka_user_id:
        try:
            client = _build_planka_client()
            binding = PlankaIssueBinding.objects.filter(issue=issue).first()

            # If membership IDs aren't stored, look them up via the API
            if binding and not contributor.planka_membership_id:
                found = client.find_board_membership(binding.board_id, contributor.planka_user_id)
                if found:
                    contributor.planka_membership_id = str(found.get("id", ""))

            if binding and binding.instructions_board_id and not contributor.planka_instructions_membership_id:
                found = client.find_board_membership(binding.instructions_board_id, contributor.planka_user_id)
                if found:
                    contributor.planka_instructions_membership_id = str(found.get("id", ""))

            for membership_id in [contributor.planka_membership_id, contributor.planka_instructions_membership_id]:
                if membership_id:
                    client.remove_board_member(membership_id)
            contributor.planka_membership_id = ""
            contributor.planka_instructions_membership_id = ""
            contributor.planka_sync_state = IssueContributor.PlankaSyncState.OK
        except PlankaAPIError as error:
            contributor.planka_sync_state = IssueContributor.PlankaSyncState.ERROR
            contributor.planka_last_error = _safe_planka_error(error)
            messages.warning(request, f"Could not remove Planka access: {contributor.planka_last_error}")

    contributor.status = IssueContributor.Status.REVOKED
    contributor.revoked_at = now
    contributor.save(
        update_fields=[
            "status",
            "revoked_at",
            "planka_membership_id",
            "planka_instructions_membership_id",
            "planka_sync_state",
            "planka_last_error",
            "modified",
        ]
    )

    IssueContributorInvite.objects.filter(
        contributor=contributor,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)

    messages.success(request, f"Access revoked for {contributor.email}.")
    return _render_issue_contributors_panel(request, issue, role=contributor.role)


def issue_invite_accept(request, token):
    token_hash = IssueContributorInvite.hash_token(token)
    invite = (
        IssueContributorInvite.objects.select_related("contributor", "contributor__issue", "contributor__user")
        .filter(token_hash=token_hash)
        .first()
    )

    context = {
        "invite": invite,
        "status": "invalid",
        "status_message": "This invite link is invalid.",
    }
    if not invite:
        return render(request, "backend/invites/accept_issue_contributor_invite.html", context)

    contributor = invite.contributor
    now = timezone.now()

    if contributor.status == IssueContributor.Status.REVOKED:
        context["status_message"] = "This invite has been revoked."
        return render(request, "backend/invites/accept_issue_contributor_invite.html", context)

    if invite.expires_at <= now:
        context["status"] = "expired"
        context["status_message"] = "This invite has expired. Please ask for a new invite link."
        return render(request, "backend/invites/accept_issue_contributor_invite.html", context)

    if not request.user.is_authenticated:
        User = get_user_model()
        account_exists = User.objects.filter(email=contributor.email).exists()
        invite_path = request.get_full_path()
        # Store token + email in session:
        # - token: lets AccountAdapter.is_open_for_signup validate the invite
        # - email: lets InviteAwareLoginView/SignupView pre-fill & lock the email field
        request.session["_pending_invite_token"] = token
        request.session["pending_invite_email"] = contributor.email
        context["status"] = "unauthenticated"
        context["invited_email"] = contributor.email
        context["account_exists"] = account_exists
        context["login_url"] = f"{reverse('account_login')}?next={invite_path}"
        context["signup_url"] = f"{reverse('account_signup')}?next={invite_path}"
        return render(request, "backend/invites/accept_issue_contributor_invite.html", context)

    expected_email = (contributor.email or "").strip().lower()
    user_email = (request.user.email or "").strip().lower()

    if expected_email != user_email:
        from django.contrib.auth import logout as auth_logout

        auth_logout(request)
        messages.info(
            request,
            f"You've been signed out. Please sign in or create an account"
            f" with {contributor.email} to accept this invite.",
        )
        return redirect(request.get_full_path())

    if (
        invite.consumed_at
        and contributor.user_id == request.user.pk
        and contributor.status == IssueContributor.Status.ACTIVE
    ):
        context["status"] = "accepted"
        context["status_message"] = "Invite already accepted. You already have access."
        return render(request, "backend/invites/accept_issue_contributor_invite.html", context)

    with transaction.atomic():
        contributor.user = request.user
        contributor.status = IssueContributor.Status.ACTIVE
        contributor.accepted_at = now
        contributor.revoked_at = None
        contributor.save(update_fields=["user", "status", "accepted_at", "revoked_at", "modified"])

        invite.consumed_at = now
        invite.save(update_fields=["consumed_at", "modified"])

        # Auto-link Author profile by email if not already linked.
        if not contributor.author_id:
            from spanza_journal_watch.submissions.models import Author as AuthorModel

            matched_author = AuthorModel.objects.filter(email=contributor.email).first()
            if matched_author:
                contributor.author = matched_author
                contributor.save(update_fields=["author", "modified"])

        # Populate the user's display name from the invite if not already set,
        # so that OIDC name claims (used by Planka SSO) reflect their real name.
        contributor_name = (contributor.name or "").strip()
        if contributor_name and not (getattr(request.user, "name", "") or "").strip():
            request.user.name = contributor_name
            request.user.save(update_fields=["name"])

        # Grant permissions based on contributor role
        import logging

        from django.contrib.auth.models import Permission as DjangoPerm

        logger = logging.getLogger(__name__)

        # All accepted contributors can recommend articles
        perms_to_grant = [
            ("submissions", "can_recommend"),
        ]
        # Coordinators also get backend access
        if contributor.role == IssueContributor.Role.COORDINATOR:
            perms_to_grant += [
                ("submissions", "regional_coordinator"),
                ("submissions", "manage_issue_builder"),
            ]

        granted_count = 0
        for app_label, codename in perms_to_grant:
            try:
                perm = DjangoPerm.objects.get(content_type__app_label=app_label, codename=codename)
                request.user.user_permissions.add(perm)
                granted_count += 1
            except DjangoPerm.DoesNotExist:
                logger.error(
                    "Permission %s.%s not found when accepting invite — " "run migrations to create it.",
                    app_label,
                    codename,
                )
        # Clear Django's per-request permission cache so subsequent has_perm() calls
        # in this same request see the newly granted permissions.
        for attr in ("_perm_cache", "_user_perm_cache"):
            request.user.__dict__.pop(attr, None)
        if contributor.role == IssueContributor.Role.COORDINATOR and granted_count and not request.user.is_staff:
            request.user.is_staff = True
            request.user.save(update_fields=["is_staff"])

    # Mark the user's email as verified — the invite link is proof of email ownership.
    from allauth.account.models import EmailAddress

    EmailAddress.objects.update_or_create(
        user=request.user,
        email=request.user.email,
        defaults={"verified": True, "primary": True},
    )
    # Clear pending invite session keys now that the invite is consumed.
    request.session.pop("_pending_invite_token", None)
    request.session.pop("pending_invite_email", None)

    _sync_contributor_to_planka(contributor)

    try:
        _send_issue_welcome_email(request, contributor)
    except Exception:
        pass  # Welcome email is best-effort; don't block acceptance

    planka_base_url = getattr(settings, "PLANKA_EXTERNAL_URL", "") or getattr(settings, "PLANKA_BASE_URL", "")
    context["status"] = "accepted"
    context["status_message"] = "Invite accepted. Your access is now active."
    context["issue"] = contributor.issue
    context["planka_base_url"] = planka_base_url
    context["is_coordinator"] = contributor.role == IssueContributor.Role.COORDINATOR
    return render(request, "backend/invites/accept_issue_contributor_invite.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def publish_issue_bundle(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    errors = _validate_issue_publish(issue)

    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")

    with transaction.atomic():
        issue.active = True
        issue.save(update_fields=["active", "modified"])

        reviews = issue.reviews.select_related("article").all()
        for review in reviews:
            if not review.article.active:
                review.article.active = True
                review.article.save(update_fields=["active", "modified"])
            if not review.active:
                review.active = True
                review.save()

        transaction.on_commit(bump_content_cache_version)

    messages.success(request, "Issue, reviews, and articles are now live.")
    return redirect(f"{reverse('backend:issue_publish')}?issue={issue.pk}")


def _provision_planka_project(client, project_name, background_asset=None):
    """
    Create a Planka project, Reviews board, Instructions board (with lists and
    instruction cards), and optional background image.

    Returns a dict with keys: project, board, instructions_board,
    list_mapping, instruction_list_mapping.
    """
    project = client.create_project(project_name)

    if background_asset:
        with background_asset.image.open("rb") as image_file:
            background_image = client.upload_project_background_image(
                project["id"],
                image_file,
                filename=Path(background_asset.image.name).name,
                content_type="image/webp",
            )
        background_image_id = background_image.get("id")
        if background_image_id:
            client.update_project_background(
                project["id"],
                background_type="image",
                background_image_id=background_image_id,
            )

    board = client.create_board(project["id"], name="Reviews")
    instructions_board = client.create_board(project["id"], name="Instructions", position=2 * 65536)

    list_mapping = {}
    for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
        list_obj = client.create_list(
            board_id=board["id"],
            name=PLANKA_LIST_LABELS[key],
            position=index * 65536,
            list_type="active",
        )
        list_color = PLANKA_LIST_COLORS.get(key)
        if list_color:
            client.update_list(list_obj["id"], color=list_color)
        list_mapping[key] = list_obj["id"]

    instruction_cards = _load_instruction_cards_by_bucket()
    instruction_list_mapping = {}
    for index, key in enumerate(PLANKA_INSTRUCTIONS_LIST_ORDER, start=1):
        list_obj = client.create_list(
            board_id=instructions_board["id"],
            name=PLANKA_INSTRUCTIONS_LIST_LABELS[key],
            position=index * 65536,
            list_type="active",
        )
        list_color = PLANKA_INSTRUCTIONS_LIST_COLORS.get(key)
        if list_color:
            client.update_list(list_obj["id"], color=list_color)
        instruction_list_mapping[key] = list_obj["id"]

        cards_for_list = instruction_cards.get(key, [])
        for card_index, card in enumerate(cards_for_list, start=1):
            client.create_card(
                list_id=list_obj["id"],
                name=card["title"],
                description=card["body"],
                position=card_index * 65536,
                card_type="story",
            )

    return {
        "project": project,
        "board": board,
        "instructions_board": instructions_board,
        "list_mapping": list_mapping,
        "instruction_list_mapping": instruction_list_mapping,
    }


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_setup_issue_project(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    if hasattr(issue, "planka_binding"):
        return _render_planka_panel(
            request,
            issue,
            panel_status="This issue is already linked to a Planka project.",
            panel_status_level="info",
        )

    form = PlankaProjectSetupForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_planka_panel(
            request,
            issue,
            panel_status="Project name is required.",
            panel_status_level="danger",
        )

    project_name = form.cleaned_data["project_name"]
    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        return _render_planka_panel(
            request,
            issue,
            panel_status=str(error),
            panel_status_level="danger",
        )
    try:
        client = _build_planka_client()
        result = _provision_planka_project(client, project_name, background_asset=background_asset)
    except (KeyError, PlankaAPIError) as error:
        return _render_planka_panel(
            request,
            issue,
            panel_status=f"Unable to set up Planka project: {error}",
            panel_status_level="danger",
        )

    project = result["project"]
    board = result["board"]
    instructions_board = result["instructions_board"]
    list_mapping = result["list_mapping"]
    instruction_list_mapping = result["instruction_list_mapping"]

    new_binding = PlankaIssueBinding.objects.create(
        issue=issue,
        project_id=project["id"],
        project_name=project_name,
        board_id=board["id"],
        board_name=board.get("name") or "Reviews",
        instructions_board_id=instructions_board["id"],
        instructions_board_name=instructions_board.get("name") or "Instructions",
        lists=list_mapping,
        instructions_lists=instruction_list_mapping,
        custom_fields={},
        custom_field_group_id=None,
        background_asset=background_asset,
    )
    _register_planka_webhook(client, new_binding)

    if request.POST.get("from_setup_page"):
        from django.http import HttpResponse as _HttpResponse

        response = _HttpResponse()
        response["HX-Redirect"] = f"{reverse('backend:issue_builder')}?issue={issue.pk}"
        return response

    return _render_planka_panel(
        request,
        issue,
        panel_status="Planka project linked to this issue.",
        panel_status_level="success",
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_recreate_issue_board(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_scope = (request.POST.get("card_scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"

    try:
        client = _build_planka_client()

        # Check whether the project still exists. A 404 means the whole project
        # is gone, so we do a full rebuild instead of just recreating the board.
        project_gone = False
        try:
            client.get_project(binding.project_id)
        except PlankaAPIError as probe_error:
            if _is_planka_board_not_found_error(probe_error):
                project_gone = True
            else:
                raise

        if project_gone:
            # Full rebuild: new project + Reviews board + Instructions board
            result = _provision_planka_project(
                client,
                binding.project_name or issue.name,
                background_asset=binding.background_asset,
            )
            binding.project_id = result["project"]["id"]
            binding.project_name = result["project"].get("name") or binding.project_name
            binding.board_id = result["board"]["id"]
            binding.board_name = result["board"].get("name") or "Reviews"
            binding.instructions_board_id = result["instructions_board"]["id"]
            binding.instructions_board_name = result["instructions_board"].get("name") or "Instructions"
            binding.lists = result["list_mapping"]
            binding.instructions_lists = result["instruction_list_mapping"]
            binding.save(
                update_fields=[
                    "project_id",
                    "project_name",
                    "board_id",
                    "board_name",
                    "instructions_board_id",
                    "instructions_board_name",
                    "lists",
                    "instructions_lists",
                    "modified",
                ]
            )
            _register_planka_webhook(client, binding)
            status_msg = "Planka project, Reviews board, and Instructions board recreated from scratch."
        else:
            # Project exists — recreate only the Reviews board
            board = client.create_board(binding.project_id, name=(binding.board_name or "Reviews"))
            list_mapping = {}
            for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
                list_obj = client.create_list(
                    board_id=board["id"],
                    name=PLANKA_LIST_LABELS[key],
                    position=index * 65536,
                    list_type="active",
                )
                list_color = PLANKA_LIST_COLORS.get(key)
                if list_color:
                    client.update_list(list_obj["id"], color=list_color)
                list_mapping[key] = list_obj["id"]

            binding.board_id = board["id"]
            binding.board_name = board.get("name") or "Reviews"
            binding.lists = list_mapping
            binding.save(update_fields=["board_id", "board_name", "lists", "modified"])
            _register_planka_webhook(client, binding)
            status_msg = "Reviews board recreated and remapped for this issue."

        # Re-sync all contributors (invited + active) so they have access to the new board.
        contributors_to_sync = IssueContributor.objects.filter(
            issue=issue,
            status__in=[IssueContributor.Status.INVITED, IssueContributor.Status.ACTIVE],
        )
        sync_errors = []
        for contributor in contributors_to_sync:
            ok, err = _sync_contributor_to_planka(contributor)
            if not ok:
                sync_errors.append(f"{contributor.email}: {err}")
        if sync_errors:
            status_msg += f" Warning: {len(sync_errors)} contributor(s) could not be synced."

        board_cards = _extract_board_cards(binding)
        scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=scoped_cards,
            panel_status=status_msg,
            panel_status_level="success",
            planka_card_scope=card_scope,
            planka_scope_counts=_build_planka_scope_counts(board_cards),
        )
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=[],
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else f"Could not recreate Reviews board: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=_is_planka_board_not_found_error(error),
        )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_update_project_name(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectNameForm(request.POST)
    redirect_url = f"{reverse('backend:issue_planka_import')}?issue={issue.pk}"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please enter a valid project name.",
                card_status_level="danger",
            )
        messages.error(request, "Please enter a valid project name.")
        return redirect(redirect_url)

    project_name = form.cleaned_data["project_name"]

    try:
        client = _build_planka_client()
        client.update_project_name(binding.project_id, project_name)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not rename project: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not rename project: {safe_error}")
        return redirect(redirect_url)

    binding.project_name = project_name
    binding.save(update_fields=["project_name", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Project name updated.",
            card_status_level="success",
        )

    messages.success(request, "Project name updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_update_project_background(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectBackgroundForm(request.POST, request.FILES)
    redirect_url = f"{reverse('backend:issue_planka_import')}?issue={issue.pk}"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please fix background image form errors.",
                card_status_level="danger",
            )
        messages.error(request, "Please fix background image form errors.")
        return redirect(redirect_url)

    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=str(error),
                card_status_level="danger",
            )
        messages.error(request, str(error))
        return redirect(redirect_url)

    if not background_asset:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Select or upload a background image first.",
                card_status_level="info",
            )
        messages.info(request, "Select or upload a background image first.")
        return redirect(redirect_url)

    try:
        client = _build_planka_client()
        with background_asset.image.open("rb") as image_file:
            background_image = client.upload_project_background_image(
                binding.project_id,
                image_file,
                filename=Path(background_asset.image.name).name,
                content_type="image/webp",
            )

        background_image_id = background_image.get("id")
        if not background_image_id:
            raise PlankaAPIError("Planka did not return a background image id.")

        client.update_project_background(
            binding.project_id,
            background_type="image",
            background_image_id=background_image_id,
        )
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not update background image: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not update background image: {safe_error}")
        return redirect(redirect_url)

    binding.background_asset = background_asset
    binding.save(update_fields=["background_asset", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Background image updated.",
            card_status_level="success",
        )

    messages.success(request, "Background image updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_refresh_publish_cards(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)

    card_scope = (request.GET.get("scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"

    try:
        board_cards = _extract_board_cards(binding)
        scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
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
                else f"Could not refresh Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=board_missing,
        )

    summary = _build_planka_publish_summary(scoped_cards)
    return _render_planka_panel(
        request,
        issue,
        publish_cards=scoped_cards,
        panel_status=(
            f"Refresh complete. {summary['total']} cards loaded in this view "
            f"({summary['valid']} ready, {summary['missing']} with missing fields, "
            f"{summary['already_imported']} already imported/protected)."
        ),
        panel_status_level="success",
        planka_card_scope=card_scope,
        planka_scope_counts=_build_planka_scope_counts(board_cards),
    )


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

    schema = selected["schema"]
    source_article = linked_batch_row.article if linked_batch_row else None
    metadata_manual_review_required = source_article is None

    article_url = ""
    article_name = (selected.get("name") or "Untitled article").strip()
    article_citation = ""
    article_year = datetime.date.today().year
    journal_name = ""

    if source_article:
        article_name = (source_article.title or article_name).strip()
        article_url = (source_article.article_url or source_article.pubmed_url or "").strip()
        article_citation = _build_pubmed_article_citation(source_article)
        journal_name = (source_article.source_journal_name or "").strip()
        if source_article.publication_date:
            article_year = source_article.publication_date.year
        elif source_article.publication_month:
            article_year = source_article.publication_month.year

    journal = None
    if journal_name:
        journal, _ = Journal.objects.get_or_create(name=journal_name)

    article = PubmedArticle.objects.create(
        title=article_name,
        journal=journal,
        publication_date=datetime.date(article_year, 1, 1),
        citation=article_citation,
        article_url=article_url or "",
        tags_string="",
        active=False,
    )

    # --- Resolve reviewer (review.author) ---
    # Primary:  card member(s) — the user(s) assigned to the card.
    # Fallback: the most-recent user who edited the card description (via actions).
    # In both cases we match the Planka user's email against IssueContributors for
    # this issue, since contributors have a linked Author profile.
    author = None

    client = _build_planka_client()
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
        board_cards = _extract_board_cards(binding)
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

    refreshed_board_cards = _extract_board_cards(binding)
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
        board_cards = _extract_board_cards(binding)
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
                    "Review already created from this card. " "This card is protected and will not be re-imported."
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

        refreshed_board_cards = _extract_board_cards(binding)
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
    Auth: if PLANKA_WEBHOOK_SECRET is set, verifies Authorization: Bearer <secret>.
    """
    secret = (getattr(settings, "PLANKA_WEBHOOK_SECRET", "") or "").strip()
    if secret:
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
        client = _build_planka_client()
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
        client = _build_planka_client()
        client._request("PATCH", f"/cards/{revision.card_id}", json={"description": revision.description})
    except PlankaAPIError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def inbox(request):
    from .models import EmailThread

    query = (request.GET.get("q") or "").strip()
    threads = EmailThread.objects.prefetch_related("inbound_messages", "sent_messages")
    if query:
        threads = threads.filter(Q(external_address__icontains=query) | Q(subject__icontains=query))

    paginator = Paginator(threads, 30)
    page = paginator.get_page(request.GET.get("page"))
    context = {"page": page, "query": query}
    if request.headers.get("HX-Request") == "true":
        return render(request, "backend/_inbox_thread_list.html", context)
    return render(request, "backend/inbox.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def inbox_mark_all_read(request):
    from .models import EmailThread, InboundEmail

    query = (request.POST.get("q") or "").strip()
    threads = EmailThread.objects.all()
    if query:
        threads = threads.filter(Q(external_address__icontains=query) | Q(subject__icontains=query))

    thread_ids = list(threads.filter(has_unread=True).values_list("id", flat=True))
    if thread_ids:
        InboundEmail.objects.filter(thread_id__in=thread_ids, read=False).update(read=True)
        EmailThread.objects.filter(id__in=thread_ids).update(has_unread=False)

    if request.headers.get("HX-Request") == "true":
        mutable_get = request.GET.copy()
        if query:
            mutable_get["q"] = query
        else:
            mutable_get.pop("q", None)
        request.GET = mutable_get
        return inbox(request)

    messages.success(request, "All visible inbox threads marked as read.")
    if query:
        return redirect(f"{reverse('backend:inbox')}?q={query}")
    return redirect("backend:inbox")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def inbox_thread(request, thread_id):
    from .models import EmailThread

    thread = get_object_or_404(EmailThread, pk=thread_id)

    inbound_msgs = list(thread.inbound_messages.order_by("sent_timestamp", "pk"))
    sent_msgs = list(thread.sent_messages.order_by("created"))

    # Interleave into a unified chronological timeline
    timeline = sorted(
        [{"kind": "inbound", "obj": m, "at": m.sent_timestamp or m.created} for m in inbound_msgs]
        + [{"kind": "sent", "obj": m, "at": m.created} for m in sent_msgs],
        key=lambda x: x["at"],
    )

    # Mark thread as read
    if thread.has_unread:
        thread.inbound_messages.filter(read=False).update(read=True)
        thread.has_unread = False
        thread.save(update_fields=["has_unread"])

    return render(request, "backend/inbox_thread.html", {"thread": thread, "timeline": timeline})


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def inbox_reply(request, thread_id):
    from email.utils import make_msgid, parseaddr

    from django.core.mail import EmailMessage

    from .models import EmailThread, SentEmail

    thread = get_object_or_404(EmailThread, pk=thread_id)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Reply body cannot be empty.")
        return redirect("backend:inbox_thread", thread_id=thread_id)

    subject = thread.subject or "(no subject)"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    from_email = _get_inbox_from_email()
    from_address = parseaddr(from_email)[1]
    from_domain = from_address.split("@")[-1].strip() if "@" in from_address else ""

    # Generate a Message-ID we track so future replies can be threaded
    msg_id = make_msgid(domain=from_domain or "journalwatch.org.au")

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[thread.external_address],
        headers={"Message-ID": msg_id},
    )
    try:
        email.send()
    except Exception:
        logger.exception("Failed to send inbox reply to %s (thread %s)", thread.external_address, thread_id)
        messages.error(request, "Failed to send reply. Please try again.")
        return redirect("backend:inbox_thread", thread_id=thread_id)

    SentEmail.objects.create(
        thread=thread,
        recipient=thread.external_address,
        subject=subject,
        body=body,
        message_id=msg_id,
        sent_by=request.user,
    )
    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])

    messages.success(request, f"Reply sent to {thread.external_address}.")
    return redirect("backend:inbox_thread", thread_id=thread_id)


# Docs
# ---------------------------------------------------------------------------

_DOCS_ROOT = Path(settings.BASE_DIR) / "docs" / "_build" / "html"


@login_required
def serve_docs(request, path=""):
    """Serve the built Sphinx documentation, protected by login."""
    if not path:
        path = "index.html"
    return static_serve(request, path, document_root=str(_DOCS_ROOT))


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
