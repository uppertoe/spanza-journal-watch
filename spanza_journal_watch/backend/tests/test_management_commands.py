import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command

pytestmark = pytest.mark.django_db


class TestSetupPlankaOidcCommand:
    def test_creates_application_from_environment(self, monkeypatch):
        from oauth2_provider.models import Application

        monkeypatch.setenv("OIDC_CLIENT_ID", "planka-staging")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "staging-secret")
        monkeypatch.setenv("PLANKA_EXTERNAL_URL", "https://planka.staging.journalwatch.org.au")

        call_command("setup_planka_oidc", no_color=True)

        app = Application.objects.get(client_id="planka-staging")
        assert app.client_secret != "staging-secret"
        assert check_password("staging-secret", app.client_secret) is True
        assert app.redirect_uris == "https://planka.staging.journalwatch.org.au/oidc-callback"
        assert app.allowed_origins == "https://planka.staging.journalwatch.org.au"
        assert app.skip_authorization is True

    def test_updates_existing_application_to_match_environment(self, monkeypatch):
        from oauth2_provider.models import Application

        app = Application.objects.create(
            name="Old Planka App",
            client_id="planka-production",
            client_secret="old-secret",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_IMPLICIT,
            redirect_uris="https://old.example.com/oidc-callback",
            allowed_origins="https://old.example.com",
            algorithm="HS256",
            skip_authorization=False,
        )

        monkeypatch.setenv("OIDC_CLIENT_ID", "planka-production")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "new-secret")
        monkeypatch.setenv("PLANKA_EXTERNAL_URL", "https://planka.staging.journalwatch.org.au")

        call_command("setup_planka_oidc", no_color=True)

        app.refresh_from_db()
        assert app.name == "Planka (planka-production)"
        assert app.client_secret != "new-secret"
        assert check_password("new-secret", app.client_secret) is True
        assert app.client_type == Application.CLIENT_CONFIDENTIAL
        assert app.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE
        assert app.redirect_uris == "https://planka.staging.journalwatch.org.au/oidc-callback"
        assert app.allowed_origins == "https://planka.staging.journalwatch.org.au"
        assert app.algorithm == "RS256"
        assert app.skip_authorization is True
