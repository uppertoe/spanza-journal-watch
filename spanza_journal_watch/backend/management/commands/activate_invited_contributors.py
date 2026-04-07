"""
Activate INVITED contributors who already have a matching user account.

When a reviewer creates an account after clicking their invite link but the
redirect back to the acceptance URL fails (e.g. session lost, browser closed),
the contributor record stays in INVITED status even though the user exists.

This command finds those contributors and replays the acceptance logic:
  - Links the contributor to the matching Django user
  - Sets status to ACTIVE
  - Grants the appropriate permissions
  - Auto-links an Author profile if one exists with the same email
  - Syncs the contributor to the Planka board
  - Marks the invite as consumed

Usage:
    python manage.py activate_invited_contributors          # dry-run (default)
    python manage.py activate_invited_contributors --apply  # apply changes
"""

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from spanza_journal_watch.backend.models import IssueContributor, IssueContributorInvite

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Activate INVITED contributors whose email matches an existing user account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually apply changes. Without this flag, only a dry-run report is shown.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]

        candidates = IssueContributor.objects.filter(
            status=IssueContributor.Status.INVITED,
            user__isnull=True,
        ).select_related("issue")

        matched = []
        for contributor in candidates:
            user = User.objects.filter(email__iexact=contributor.email).first()
            if user:
                matched.append((contributor, user))

        if not matched:
            self.stdout.write("No INVITED contributors with matching user accounts found.")
            return

        self.stdout.write(f"Found {len(matched)} contributor(s) to activate:\n")
        for contributor, user in matched:
            self.stdout.write(
                f"  {contributor.name} ({contributor.email})"
                f"  role={contributor.role}  issue={contributor.issue.name}"
                f"  → user pk={user.pk}"
            )

        if not apply:
            self.stdout.write(self.style.WARNING("\nDry run — no changes made. Pass --apply to activate."))
            return

        self.stdout.write("")
        now = timezone.now()

        for contributor, user in matched:
            label = f"{contributor.name} ({contributor.email})"
            try:
                self._activate_contributor(contributor, user, now)
                self.stdout.write(self.style.SUCCESS(f"  Activated: {label}"))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  FAILED: {label} — {exc}"))
                logger.exception("Failed to activate contributor %s", contributor.pk)

    def _activate_contributor(self, contributor, user, now):
        with transaction.atomic():
            contributor.user = user
            contributor.status = IssueContributor.Status.ACTIVE
            contributor.accepted_at = now
            contributor.revoked_at = None
            contributor.save(update_fields=["user", "status", "accepted_at", "revoked_at", "modified"])

            # Mark the invite as consumed.
            invite = (
                IssueContributorInvite.objects.filter(contributor=contributor, consumed_at__isnull=True)
                .order_by("-created")
                .first()
            )
            if invite:
                invite.consumed_at = now
                invite.save(update_fields=["consumed_at", "modified"])

            # Auto-link Author profile by email.
            if not contributor.author_id:
                from spanza_journal_watch.submissions.models import Author

                matched_author = Author.objects.filter(email=contributor.email).first()
                if matched_author:
                    contributor.author = matched_author
                    contributor.save(update_fields=["author", "modified"])

            # Populate user display name if not set.
            contributor_name = (contributor.name or "").strip()
            if contributor_name and not (getattr(user, "name", "") or "").strip():
                user.name = contributor_name
                user.save(update_fields=["name"])

            # Grant permissions.
            perms_to_grant = [
                ("submissions", "can_recommend"),
                ("submissions", "invited_contributor"),
            ]
            if contributor.role == IssueContributor.Role.COORDINATOR:
                perms_to_grant += [
                    ("submissions", "regional_coordinator"),
                    ("submissions", "manage_issue_builder"),
                ]

            for app_label, codename in perms_to_grant:
                try:
                    perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
                    user.user_permissions.add(perm)
                except Permission.DoesNotExist:
                    logger.error("Permission %s.%s not found — run migrations.", app_label, codename)

            if contributor.role == IssueContributor.Role.COORDINATOR and not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])

        # Mark email as verified (outside the atomic block — non-critical).
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

        # Sync to Planka (outside the atomic block — network call).
        from spanza_journal_watch.backend.views import _sync_contributor_to_planka

        success, error = _sync_contributor_to_planka(contributor)
        if not success:
            self.stderr.write(self.style.WARNING(f"    Planka sync issue: {error}"))
