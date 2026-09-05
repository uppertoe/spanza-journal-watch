"""The Settings page: inbox sender, banner, invites, Planka set-up runs and fetch monitoring."""

import datetime
import logging
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Avg
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    BackendPreferenceFrontendBannerForm,
    BackendPreferenceInboxSettingsForm,
    PubmedApiKeyForm,
)
from ..models import (
    BackendPreference,
    ChiefEditorInvite,
    FetchLog,
    PubmedIntegrationCredential,
)
from ..planka import PlankaAPIError, PlankaClient
from . import shared
from .planka_boards import _build_planka_webhook_url
from .shared import _get_planka_integration_credential, _safe_planka_error

logger = logging.getLogger(__name__)


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

    # Webhook config checks
    planka_callback_url = (getattr(settings, "PLANKA_CALLBACK_BASE_URL", "") or "").strip()
    planka_webhook_secret = (getattr(settings, "PLANKA_WEBHOOK_SECRET", "") or "").strip()
    planka_webhook_status = None  # None = not checked, dict with details
    if planka_connected and planka_callback_url:
        expected_url = _build_planka_webhook_url()
        try:
            webhooks = client.list_webhooks()
            matching = [w for w in webhooks if w.get("url") == expected_url]
            if matching:
                wh = matching[0]
                planka_webhook_status = {
                    "registered": True,
                    "id": wh.get("id"),
                    "url": wh.get("url"),
                    "events": wh.get("events") or [],
                }
            else:
                planka_webhook_status = {"registered": False, "expected_url": expected_url}
        except PlankaAPIError as exc:
            planka_webhook_status = {"registered": False, "error": _safe_planka_error(exc)}

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
        "planka_callback_url": planka_callback_url,
        "planka_webhook_secret_set": bool(planka_webhook_secret),
        "planka_webhook_status": planka_webhook_status,
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
    from ..tasks import refresh_mesh_terms_task

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
        from django.contrib.auth import logout

        logout(request)
        request.session["_pending_invite_token"] = token
        request.session["pending_invite_email"] = invite.email
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
            ("submissions", "invited_contributor"),
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
        client = shared._build_planka_client()
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
