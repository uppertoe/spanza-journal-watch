"""
Tests for users/signals.py.

Covers:
1. revoke_planka_sessions_on_logout — no-op when user is None
2. _revoke_planka_db_sessions — no-op when PLANKA_DB_URL is empty
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from spanza_journal_watch.users.signals import (
    _revoke_planka_db_sessions,
    revoke_planka_sessions_on_logout,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


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
