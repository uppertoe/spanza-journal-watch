"""
Management command to create or promote a chief editor account.

Usage:
    python manage.py create_chief_editor user@example.com [--name "Full Name"] [--password]

If the user already exists, the command grants them chief editor permissions.
If they don't exist, it creates the account and prompts for a password.
"""

import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

CHIEF_EDITOR_PERMISSIONS = [
    "submissions.chief_editor",
    "submissions.manage_issue_builder",
    "backend.manage_subscriber_csv",
    "backend.send_newsletters",
    "backend.view_newsletter_stats",
    "backend.view_site_analytics",
]


def _get_permission(codename_with_app):
    app_label, codename = codename_with_app.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


class Command(BaseCommand):
    help = "Create or promote a user to chief editor role."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email address of the chief editor")
        parser.add_argument("--name", default="", help="Full name (for new accounts)")
        parser.add_argument(
            "--password",
            action="store_true",
            help="Prompt for a password even if the account already exists",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        name = options.get("name", "").strip()

        user, created = User.objects.get_or_create(
            email=email,
            defaults={"username": email, "name": name or email.split("@")[0]},
        )

        if created:
            self.stdout.write(f"Creating new account: {email}")
            password = getpass.getpass("Password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                user.delete()
                raise CommandError("Passwords do not match.")
            user.set_password(password)
            user.is_staff = True
            user.save()
        else:
            self.stdout.write(f"Existing account found: {email}")
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
            if options["password"]:
                password = getpass.getpass("New password: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    raise CommandError("Passwords do not match.")
                user.set_password(password)
                user.save(update_fields=["password"])

        # Grant all chief editor permissions
        granted = []
        for perm_str in CHIEF_EDITOR_PERMISSIONS:
            try:
                perm = _get_permission(perm_str)
                user.user_permissions.add(perm)
                granted.append(perm_str)
            except Permission.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"  Permission not found (run migrations?): {perm_str}"))

        self.stdout.write(self.style.SUCCESS(f"\n{'Created' if created else 'Updated'} chief editor: {email}"))
        if granted:
            self.stdout.write("Permissions granted:")
            for p in granted:
                self.stdout.write(f"  + {p}")
        self.stdout.write("\nThe user can now log in at /editorial/ and access all chief editor functions.")
