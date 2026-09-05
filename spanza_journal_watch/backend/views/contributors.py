"""Issue contributors: invitations, acceptance and revocation."""

import datetime
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.submissions.models import (
    Author,
    HealthService,
    Issue,
)

from ..models import (
    IssueContributor,
    IssueContributorInvite,
    PlankaIssueBinding,
)
from ..planka import PlankaAPIError
from . import planka_boards, shared
from .issue_context import _issue_builder_base_context, _issue_invite_ttl_days
from .shared import _check_coordinator_issue_access, _safe_planka_error

logger = logging.getLogger(__name__)


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

    role_label = contributor.get_role_display().lower()
    subject = f"Invitation to be a {role_label} for {issue.name}"
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
    role_label = contributor.get_role_display().lower()
    subject = f"Welcome as a {role_label} for {issue.name}"
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


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def contributor_author_lookup(request):
    """JSON: look up an existing Author by email, returning name and affiliations."""

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

        planka_ok, planka_error = planka_boards._sync_contributor_to_planka(contributor)
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
    send_all_pending = request.POST.get("send_all_pending") == "1"
    panel_role = _panel_role_from_request(request)

    if send_all_pending:
        contributors = IssueContributor.objects.filter(
            issue=issue,
            role=panel_role,
            status=IssueContributor.Status.PENDING,
        )
        if not contributors.exists():
            messages.info(request, "No pending reviewers to invite.")
            return _render_issue_contributors_panel(request, issue, role=panel_role)
    elif contributor_ids:
        contributors = IssueContributor.objects.filter(
            issue=issue,
            pk__in=contributor_ids,
        ).exclude(status=IssueContributor.Status.REVOKED)
    else:
        messages.error(request, "No contributors selected.")
        return _render_issue_contributors_panel(request, issue, role=panel_role)

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

    planka_ok, planka_error = planka_boards._sync_contributor_to_planka(contributor)
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

    ok, error = planka_boards._sync_contributor_to_planka(contributor)
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
            client = shared._build_planka_client()
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
        from django.contrib.auth import logout

        logout(request)
        request.session["_pending_invite_token"] = token
        request.session["pending_invite_email"] = contributor.email
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

        # All accepted contributors get access to the editorial landing page.
        # Recommending articles only needs a signed-in account.
        perms_to_grant = [
            ("submissions", "invited_contributor"),
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
                    "Permission %s.%s not found when accepting invite — run migrations to create it.",
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

    planka_boards._sync_contributor_to_planka(contributor)

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
