"""
Bootstrap a Planka API key and initialise the instance via direct DB writes.

This approach:
  - Works regardless of whether OIDC_ENFORCED=true is set
  - Works regardless of whether Planka is running (pure DB write, no REST call)
  - Does not require the admin user to have accepted Terms of Service in a browser
  - Accepts terms on behalf of the admin user automatically
  - Sets internal_config.is_initialized=true so SSO users can log in immediately
  - Is idempotent: re-running rotates the stored key

How it works:
  1. Writes terms_accepted_at + SHA-256 API key hash to user_account for the admin.
  2. Sets internal_config.is_initialized = true — this is the flag Planka checks
     before allowing SSO logins ("Admin login required to initialize instance").
  Both writes happen in a single transaction against Planka's Postgres.

Prerequisites:
  PLANKA_DB_URL       — connection URL for Planka's Postgres, e.g.
                        postgresql://postgres@planka_postgres/planka
  PLANKA_ADMIN_EMAIL  — the admin user's email (DEFAULT_ADMIN_EMAIL in Planka env)

Usage:
    python manage.py setup_planka_api_key
    python manage.py setup_planka_api_key --email admin@example.com
"""

import hashlib
import secrets
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from spanza_journal_watch.backend.models import PlankaIntegrationCredential


class Command(BaseCommand):
    help = "Generate a Planka API key and initialise the instance via direct DB writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="",
            help="Admin email (defaults to PLANKA_ADMIN_EMAIL setting).",
        )

    def handle(self, *args, **options):
        try:
            import psycopg2
        except ImportError as exc:
            raise CommandError(
                "psycopg2 is required for this command. It should already be installed "
                "as a Django database dependency."
            ) from exc

        email = (options["email"] or getattr(settings, "PLANKA_ADMIN_EMAIL", "") or "").strip()
        if not email:
            raise CommandError(
                "Admin email is required. Set PLANKA_ADMIN_EMAIL in your environment " "or pass --email."
            )

        db_url = (getattr(settings, "PLANKA_DB_URL", "") or "").strip()
        if not db_url:
            raise CommandError(
                "PLANKA_DB_URL is not configured. Set it to the connection URL for "
                "Planka's Postgres, e.g. postgresql://postgres@planka_postgres/planka"
            )

        self.stdout.write("Connecting to Planka database …")

        try:
            conn = psycopg2.connect(db_url)
        except Exception as exc:
            raise CommandError(f"Could not connect to Planka Postgres: {exc}") from exc

        try:
            with conn:
                with conn.cursor() as cur:
                    # Find the admin user by email.
                    cur.execute(
                        "SELECT id, role FROM user_account WHERE email = %s",
                        (email,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise CommandError(
                            f"No Planka user found with email {email!r}. "
                            "Ensure the Planka container has started at least once so "
                            "DEFAULT_ADMIN_EMAIL has been created."
                        )
                    user_id, role = row
                    self.stdout.write(f"  Found user id={user_id} role={role}")

                    # Generate a cryptographically random API key.
                    # Planka stores SHA-256(key) and uses the first 8 chars as a display prefix.
                    plain_key = secrets.token_hex(32)  # 64-char hex string
                    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
                    key_prefix = plain_key[:8]
                    now = datetime.now(timezone.utc)

                    # Mark terms accepted and write the API key.
                    cur.execute(
                        """
                        UPDATE user_account
                        SET
                            terms_accepted_at  = %s,
                            terms_signature    = %s,
                            api_key_prefix     = %s,
                            api_key_hash       = %s,
                            api_key_created_at = %s
                        WHERE id = %s
                        """,
                        (now, "accepted-via-setup-command", key_prefix, key_hash, now, user_id),
                    )
                    if cur.rowcount != 1:
                        raise CommandError("UPDATE affected unexpected number of rows.")

                    # Set is_initialized = true so SSO users can log in.
                    # Planka checks this flag before allowing any OIDC exchange.
                    # It is normally set when an admin first logs in via email/password,
                    # but we set it here directly to avoid that manual step.
                    cur.execute("SELECT id, is_initialized FROM internal_config LIMIT 1")
                    config_row = cur.fetchone()
                    if config_row:
                        config_id, already_initialized = config_row
                        if not already_initialized:
                            cur.execute(
                                "UPDATE internal_config SET is_initialized = true, updated_at = %s WHERE id = %s",
                                (now, config_id),
                            )
                            self.stdout.write(self.style.SUCCESS("  ✓ Instance marked as initialised."))
                        else:
                            self.stdout.write("  ✓ Instance was already initialised.")
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                "  ! internal_config row not found — Planka may not have started yet. "
                                "Re-run this command after Planka has started at least once."
                            )
                        )
        finally:
            conn.close()

        self.stdout.write(self.style.SUCCESS("  ✓ Terms marked as accepted."))
        self.stdout.write(self.style.SUCCESS("  ✓ API key written to Planka database."))

        # Store the plaintext key encrypted in Django's credential model.
        credential, created = PlankaIntegrationCredential.objects.get_or_create(
            singleton=1,
            defaults={"auth_mode": PlankaIntegrationCredential.AuthMode.API_KEY},
        )
        credential.auth_mode = PlankaIntegrationCredential.AuthMode.API_KEY
        credential.set_api_key(plain_key)
        credential.api_key_prefix = key_prefix
        credential.last_error = ""
        credential.save()

        masked = credential.get_masked_api_key()
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"  ✓ {action} Django credential (key: {masked})."))
        self.stdout.write(
            "\nDone. The X-Api-Key is active immediately and works regardless of OIDC_ENFORCED.\n"
            "SSO logins will now work. You can enable OIDC_ENFORCED to disable email/password login.\n"
            "Run setup_planka_oidc if not already done:\n"
            "  python manage.py setup_planka_oidc\n"
        )
