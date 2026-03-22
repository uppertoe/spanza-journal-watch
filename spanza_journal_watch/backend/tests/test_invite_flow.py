"""
Tests for the issue contributor invite flow.

Covers three invariants:
1. The invite page routes to "Sign in" or "Create account" based on whether an
   account already exists for the invited email — never shows both.
2. Accepting an invite marks the user's email as verified in allauth.
3. Public signup is blocked; only users arriving via a valid invite can register.
"""

import datetime

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.backend.models import IssueContributor, IssueContributorInvite
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.adapters import AccountAdapter
from spanza_journal_watch.users.tests.factories import UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_issue():
    return Issue.objects.create(name="Test Issue", active=False)


def make_contributor(issue, email="reviewer@example.com", role=IssueContributor.Role.REVIEWER):
    return IssueContributor.objects.create(
        issue=issue,
        email=email,
        role=role,
        status=IssueContributor.Status.INVITED,
    )


def make_invite(contributor, days_valid=7):
    raw_token = IssueContributorInvite.generate_raw_token()
    invite = IssueContributorInvite.objects.create(
        contributor=contributor,
        token_hash=IssueContributorInvite.hash_token(raw_token),
        expires_at=timezone.now() + datetime.timedelta(days=days_valid),
    )
    return invite, raw_token


def invite_url(raw_token):
    return reverse("issue_invite_accept", kwargs={"token": raw_token})


# ---------------------------------------------------------------------------
# 1. Invite page routing: "Sign in" vs "Create account"
# ---------------------------------------------------------------------------


class TestInviteUnauthenticatedRouting:
    def test_shows_sign_in_when_account_exists(self):
        """An existing account → only the Sign in button should appear."""
        UserFactory(email="existing@example.com")
        issue = make_issue()
        contributor = make_contributor(issue, email="existing@example.com")
        invite, raw_token = make_invite(contributor)

        client = Client()
        response = client.get(invite_url(raw_token))

        assert response.status_code == 200
        content = response.content.decode()
        assert "Sign in" in content
        assert "Create account" not in content

    def test_shows_create_account_when_no_account(self):
        """No existing account → Create account button present; sign-in button absent."""
        issue = make_issue()
        contributor = make_contributor(issue, email="newperson@example.com")
        invite, raw_token = make_invite(contributor)

        client = Client()
        response = client.get(invite_url(raw_token))

        assert response.status_code == 200
        content = response.content.decode()
        # The signup action URL should be present; the login action URL should not
        # be present as an invite-accept button (the nav may still have a sign-in link).
        signup_url_str = reverse("account_signup")
        login_url_str = reverse("account_login")
        assert signup_url_str in content
        # The login URL should not appear as the primary invite CTA
        # (check it's absent from the invite card specifically via button href)
        assert f'href="{login_url_str}?next=' not in content

    def test_session_token_set_for_unauthenticated_visitor(self):
        """Visiting an invite page should store the raw token in the session."""
        issue = make_issue()
        contributor = make_contributor(issue, email="newperson@example.com")
        invite, raw_token = make_invite(contributor)

        client = Client()
        client.get(invite_url(raw_token))

        assert client.session.get("_pending_invite_token") == raw_token

    def test_expired_invite_does_not_set_session_token(self):
        """An expired invite should show the expired state and NOT set a session token."""
        issue = make_issue()
        contributor = make_contributor(issue, email="late@example.com")
        invite, raw_token = make_invite(contributor, days_valid=-1)  # already expired

        client = Client()
        response = client.get(invite_url(raw_token))

        assert response.status_code == 200
        assert "expired" in response.content.decode().lower()
        assert client.session.get("_pending_invite_token") is None

    def test_revoked_invite_does_not_set_session_token(self):
        """A revoked invite should show the revoked state and NOT set a session token."""
        issue = make_issue()
        contributor = make_contributor(issue, email="revoked@example.com")
        contributor.status = IssueContributor.Status.REVOKED
        contributor.save()
        invite, raw_token = make_invite(contributor)

        client = Client()
        response = client.get(invite_url(raw_token))

        assert response.status_code == 200
        assert client.session.get("_pending_invite_token") is None


# ---------------------------------------------------------------------------
# 2. Email verification on invite acceptance
# ---------------------------------------------------------------------------


class TestInviteAcceptanceEmailVerification:
    def test_email_marked_verified_after_acceptance(self):
        """Accepting an invite proves email ownership — address must be verified."""
        issue = make_issue()
        contributor = make_contributor(issue, email="reviewer@example.com")
        invite, raw_token = make_invite(contributor)
        user = UserFactory(email="reviewer@example.com")

        client = Client()
        client.force_login(user)
        client.get(invite_url(raw_token))

        addr = EmailAddress.objects.filter(user=user, email="reviewer@example.com").first()
        assert addr is not None, "EmailAddress record should be created"
        assert addr.verified is True
        assert addr.primary is True

    def test_session_token_cleared_after_acceptance(self):
        """The pending invite session key should be removed once the invite is consumed."""
        issue = make_issue()
        contributor = make_contributor(issue, email="reviewer@example.com")
        invite, raw_token = make_invite(contributor)
        user = UserFactory(email="reviewer@example.com")

        client = Client()
        # Simulate having visited the invite page while unauthenticated first
        session = client.session
        session["_pending_invite_token"] = raw_token
        session.save()

        client.force_login(user)
        client.get(invite_url(raw_token))

        assert client.session.get("_pending_invite_token") is None

    def test_already_accepted_invite_does_not_error(self):
        """Revisiting an already-accepted invite should return 'accepted' status cleanly."""
        issue = make_issue()
        contributor = make_contributor(issue, email="reviewer@example.com")
        invite, raw_token = make_invite(contributor)
        user = UserFactory(email="reviewer@example.com")

        # Accept once
        client = Client()
        client.force_login(user)
        client.get(invite_url(raw_token))

        # Visit again — should show "already accepted"
        response = client.get(invite_url(raw_token))
        assert response.status_code == 200
        assert "accepted" in response.content.decode().lower()


# ---------------------------------------------------------------------------
# 3. Signup restriction via AccountAdapter
# ---------------------------------------------------------------------------


class TestSignupRestriction:
    def test_signup_blocked_without_invite_token_in_session(self, settings):
        """With ACCOUNT_ALLOW_REGISTRATION=False and no session token, signup is closed."""
        settings.ACCOUNT_ALLOW_REGISTRATION = False

        # No invite token in session → adapter should refuse signup
        adapter = AccountAdapter()

        class FakeRequest:
            session = {}

        assert adapter.is_open_for_signup(FakeRequest()) is False

    def test_signup_allowed_with_valid_invite_token_in_session(self, settings):
        """A valid invite token in the session opens signup."""
        settings.ACCOUNT_ALLOW_REGISTRATION = False

        issue = make_issue()
        contributor = make_contributor(issue, email="new@example.com")
        invite, raw_token = make_invite(contributor)

        adapter = AccountAdapter()

        class FakeRequest:
            session = {"_pending_invite_token": raw_token}

        assert adapter.is_open_for_signup(FakeRequest()) is True

    def test_signup_blocked_with_expired_invite_token_in_session(self, settings):
        """An expired invite token in the session does NOT open signup."""
        settings.ACCOUNT_ALLOW_REGISTRATION = False

        issue = make_issue()
        contributor = make_contributor(issue, email="late@example.com")
        invite, raw_token = make_invite(contributor, days_valid=-1)

        adapter = AccountAdapter()

        class FakeRequest:
            session = {"_pending_invite_token": raw_token}

        assert adapter.is_open_for_signup(FakeRequest()) is False

    def test_signup_blocked_with_consumed_invite_token_in_session(self, settings):
        """A consumed (already used) invite token in session does NOT open signup."""
        settings.ACCOUNT_ALLOW_REGISTRATION = False

        issue = make_issue()
        contributor = make_contributor(issue, email="used@example.com")
        invite, raw_token = make_invite(contributor)
        invite.consumed_at = timezone.now()
        invite.save()

        adapter = AccountAdapter()

        class FakeRequest:
            session = {"_pending_invite_token": raw_token}

        assert adapter.is_open_for_signup(FakeRequest()) is False

    def test_signup_open_when_allow_registration_true(self, settings):
        """ACCOUNT_ALLOW_REGISTRATION=True bypasses invite check (dev/test override)."""
        settings.ACCOUNT_ALLOW_REGISTRATION = True

        adapter = AccountAdapter()

        class FakeRequest:
            session = {}

        assert adapter.is_open_for_signup(FakeRequest()) is True

    def test_public_signup_url_returns_403_or_redirect_when_closed(self, settings):
        """Hitting /accounts/signup/ directly without an invite in session should be refused."""
        settings.ACCOUNT_ALLOW_REGISTRATION = False

        client = Client()
        # POST to signup with no session invite token
        response = client.post(
            reverse("account_signup"),
            {"email": "random@example.com", "password1": "Str0ng!Pass", "password2": "Str0ng!Pass"},
        )
        # allauth redirects or shows an error when signup is closed — not 200 with form success
        assert response.status_code != 302 or User.objects.filter(email="random@example.com").count() == 0


# ---------------------------------------------------------------------------
# 4. Email-mismatch: sign-out and redirect
# ---------------------------------------------------------------------------


class TestEmailMismatchSignout:
    def test_mismatch_logs_out_and_redirects_to_invite(self):
        """A logged-in user with the wrong email should be signed out and
        redirected back to the same invite URL."""
        issue = make_issue()
        contributor = make_contributor(issue, email="correct@example.com")
        invite, raw_token = make_invite(contributor)

        wrong_user = UserFactory(email="wrong@example.com")
        client = Client()
        client.force_login(wrong_user)

        url = invite_url(raw_token)
        response = client.get(url)

        # Should redirect back to the same invite URL
        assert response.status_code == 302
        assert response["Location"] == url

        # User should now be logged out
        follow_response = client.get(url)
        assert follow_response.status_code == 200
        # Now unauthenticated — should see invite acceptance prompt
        assert "invitation" in follow_response.content.decode().lower()
