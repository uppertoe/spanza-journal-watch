"""
Tests for users/signals.py.

Covers:
1. _revoke_oauth2_tokens — expires access tokens, revokes refresh tokens
2. revoke_planka_sessions_on_logout — no-op when user is None
3. _revoke_planka_db_sessions — no-op when PLANKA_DB_URL is empty
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from spanza_journal_watch.users.signals import (
    _revoke_oauth2_tokens,
    _revoke_planka_db_sessions,
    revoke_planka_sessions_on_logout,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


class TestRevokeOAuth2Tokens:
    def test_expires_active_access_tokens(self):
        from oauth2_provider.models import AccessToken, Application

        user = User.objects.create_user(email="oauth-revoke@example.com", password="pw")
        app = Application.objects.create(
            name="Test App",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            user=user,
        )
        future = timezone.now() + timedelta(hours=1)
        token = AccessToken.objects.create(
            user=user,
            token="test-access-token",
            application=app,
            expires=future,
            scope="read",
        )

        _revoke_oauth2_tokens(user)

        token.refresh_from_db()
        assert token.expires <= timezone.now()

    def test_revokes_refresh_tokens(self):
        from oauth2_provider.models import AccessToken, Application, RefreshToken

        user = User.objects.create_user(email="oauth-refresh@example.com", password="pw")
        app = Application.objects.create(
            name="Test App 2",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            user=user,
        )
        future = timezone.now() + timedelta(hours=1)
        access = AccessToken.objects.create(
            user=user,
            token="test-access-2",
            application=app,
            expires=future,
            scope="read",
        )
        refresh = RefreshToken.objects.create(
            user=user,
            token="test-refresh-token",
            application=app,
            access_token=access,
        )

        _revoke_oauth2_tokens(user)

        refresh.refresh_from_db()
        assert refresh.revoked is not None

    def test_already_expired_tokens_not_touched(self):
        from oauth2_provider.models import AccessToken, Application

        user = User.objects.create_user(email="oauth-expired@example.com", password="pw")
        app = Application.objects.create(
            name="Test App 3",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            user=user,
        )
        past = timezone.now() - timedelta(hours=1)
        token = AccessToken.objects.create(
            user=user,
            token="test-expired-token",
            application=app,
            expires=past,
            scope="read",
        )
        original_expires = token.expires

        _revoke_oauth2_tokens(user)

        token.refresh_from_db()
        assert token.expires == original_expires


class TestRevokePlankaSessionsOnLogout:
    def test_noop_when_user_is_none(self):
        """Should not raise when user is None (e.g. anonymous logout)."""
        revoke_planka_sessions_on_logout(sender=None, request=None, user=None)

    @override_settings(PLANKA_DB_URL="")
    def test_planka_db_noop_when_url_empty(self):
        """Should not attempt DB connection when PLANKA_DB_URL is empty."""
        user = User.objects.create_user(email="planka-noop@example.com", password="pw")
        # Should not raise
        _revoke_planka_db_sessions(user)

    @override_settings(PLANKA_DB_URL=None)
    def test_planka_db_noop_when_url_none(self):
        user = User.objects.create_user(email="planka-none@example.com", password="pw")
        _revoke_planka_db_sessions(user)
