"""
Register Planka as an OAuth2/OIDC application in Django's oauth2_provider.

Run once after the database is ready:

    python manage.py setup_planka_oidc

Idempotent: skips creation if an application with the same client_id already exists.
The credentials here must match OIDC_CLIENT_ID / OIDC_CLIENT_SECRET in the runtime
environment. When unset, local-friendly defaults are used.
"""

import os
from urllib.parse import urlparse

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register Planka as an OIDC client application in django-oauth-toolkit."

    @staticmethod
    def _planka_oidc_config():
        client_id = os.getenv("OIDC_CLIENT_ID", "planka-local")
        client_secret = os.getenv("OIDC_CLIENT_SECRET", "planka-local-oidc-secret-changeme")

        planka_external_url = os.getenv("PLANKA_EXTERNAL_URL", "").rstrip("/")
        redirect_uri = (
            f"{planka_external_url}/oidc-callback" if planka_external_url else "http://localhost:3001/oidc-callback"
        )

        parsed_redirect_uri = urlparse(redirect_uri)
        allowed_origins = f"{parsed_redirect_uri.scheme}://{parsed_redirect_uri.netloc}"

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "allowed_origins": allowed_origins,
            "name": f"Planka ({client_id})",
        }

    def handle(self, *args, **options):
        from oauth2_provider.models import Application

        config = self._planka_oidc_config()

        app, created = Application.objects.get_or_create(
            client_id=config["client_id"],
            defaults={
                "name": config["name"],
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "algorithm": "RS256",
                "client_secret": config["client_secret"],
                "redirect_uris": config["redirect_uri"],
                "allowed_origins": config["allowed_origins"],
                "skip_authorization": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Planka OIDC application (pk={app.pk})."))
        else:
            updated_fields = []
            expected_values = {
                "name": config["name"],
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "algorithm": "RS256",
                "client_secret": config["client_secret"],
                "redirect_uris": config["redirect_uri"],
                "allowed_origins": config["allowed_origins"],
                "skip_authorization": True,
            }
            for field_name, expected_value in expected_values.items():
                if getattr(app, field_name) != expected_value:
                    setattr(app, field_name, expected_value)
                    updated_fields.append(field_name)
            if updated_fields:
                app.save(update_fields=updated_fields)
                self.stdout.write(self.style.SUCCESS(f"Updated {', '.join(updated_fields)} (pk={app.pk})."))
            else:
                self.stdout.write(f"Planka OIDC application already exists (pk={app.pk}), skipping.")

        self.stdout.write(f"  client_id:     {app.client_id}")
        self.stdout.write(f"  redirect_uri:  {config['redirect_uri']}")
        self.stdout.write("\nOIDC discovery: http://localhost:8000/o/.well-known/openid-configuration/")
