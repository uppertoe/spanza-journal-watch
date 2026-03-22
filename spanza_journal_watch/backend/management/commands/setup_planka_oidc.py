"""
Register Planka as an OAuth2/OIDC application in Django's oauth2_provider.

Run once after the database is ready:

    python manage.py setup_planka_oidc

Idempotent: skips creation if an application with the same client_id already exists.
The credentials here must match OIDC_CLIENT_ID / OIDC_CLIENT_SECRET in local.yml.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register Planka as an OIDC client application in django-oauth-toolkit."

    def handle(self, *args, **options):
        from oauth2_provider.models import Application

        client_id = "planka-local"
        client_secret = "planka-local-oidc-secret-changeme"
        redirect_uri = "http://localhost:3001/oidc-callback"

        # allowed_origins lets DOT add CORS headers to /o/token/ for the browser-side exchange
        allowed_origins = "http://localhost:3001"

        app, created = Application.objects.get_or_create(
            client_id=client_id,
            defaults={
                "name": "Planka Local",
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "algorithm": "RS256",
                "client_secret": client_secret,
                "redirect_uris": redirect_uri,
                "allowed_origins": allowed_origins,
                "skip_authorization": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Planka OIDC application (pk={app.pk})."))
        else:
            # Ensure algorithm and allowed_origins are correct on existing applications
            updated_fields = []
            if app.algorithm != "RS256":
                app.algorithm = "RS256"
                updated_fields.append("algorithm")
            if app.allowed_origins != allowed_origins:
                app.allowed_origins = allowed_origins
                updated_fields.append("allowed_origins")
            if updated_fields:
                app.save(update_fields=updated_fields)
                self.stdout.write(self.style.SUCCESS(f"Updated {', '.join(updated_fields)} (pk={app.pk})."))
            else:
                self.stdout.write(f"Planka OIDC application already exists (pk={app.pk}), skipping.")

        self.stdout.write(f"  client_id:     {app.client_id}")
        self.stdout.write(f"  redirect_uri:  {redirect_uri}")
        self.stdout.write("\nOIDC discovery: http://localhost:8000/o/.well-known/openid-configuration/")
