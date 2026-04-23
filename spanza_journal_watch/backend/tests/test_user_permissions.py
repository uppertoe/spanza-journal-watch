"""
Smoke tests for user creation, linking, and permission flows.

Covers:
1. Freshly signed-up users have no editorial or Planka access
2. Reviewer invite acceptance grants can_recommend but NOT editorial permissions
3. Coordinator invite acceptance grants can_recommend + editorial access + is_staff
4. Accepted contributors (both roles) can access /journals
5. can_recommend permission gates the recommend action
6. Subscriber records are linked to user accounts on login
"""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.backend.models import (
    ChiefEditorInvite,
    IssueContributor,
    IssueContributorInvite,
    can_recommend_pubmed_articles,
)
from spanza_journal_watch.newsletter.models import Subscriber
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_issue():
    return Issue.objects.create(name="Test Issue", active=False)


def make_contributor(issue, email, role=IssueContributor.Role.REVIEWER):
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


def accept_invite(user, raw_token):
    """Force-login a user and visit the invite URL to accept it."""
    client = Client()
    client.force_login(user)
    response = client.get(invite_url(raw_token))
    # Refresh from DB to get updated permissions
    user.refresh_from_db()
    return response


# ---------------------------------------------------------------------------
# 1. Fresh signup has no editorial/Planka access
# ---------------------------------------------------------------------------


class TestFreshUserHasNoAccess:
    def test_no_staff_flag(self):
        user = UserFactory()
        assert user.is_staff is False

    def test_no_superuser_flag(self):
        user = UserFactory()
        assert user.is_superuser is False

    def test_no_manage_issue_builder_permission(self):
        user = UserFactory()
        assert user.has_perm("submissions.manage_issue_builder") is False

    def test_no_chief_editor_permission(self):
        user = UserFactory()
        assert user.has_perm("submissions.chief_editor") is False

    def test_no_regional_coordinator_permission(self):
        user = UserFactory()
        assert user.has_perm("submissions.regional_coordinator") is False

    def test_no_can_recommend_permission(self):
        user = UserFactory()
        assert user.has_perm("submissions.can_recommend") is False

    def test_any_authenticated_user_can_recommend(self):
        user = UserFactory()
        assert can_recommend_pubmed_articles(user) is True

    def test_cannot_access_backend(self):
        user = UserFactory()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("backend:backend_go"))
        # Non-staff users should be redirected to login or get 403
        assert response.status_code in (302, 403)


# ---------------------------------------------------------------------------
# 2. Reviewer invite grants can_recommend but NOT editorial
# ---------------------------------------------------------------------------


class TestReviewerInvitePermissions:
    def test_reviewer_gets_can_recommend(self):
        issue = make_issue()
        user = UserFactory(email="reviewer@example.com")
        contributor = make_contributor(issue, "reviewer@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        # Refresh permission cache
        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.can_recommend") is True

    def test_reviewer_cannot_manage_issues(self):
        issue = make_issue()
        user = UserFactory(email="reviewer2@example.com")
        contributor = make_contributor(issue, "reviewer2@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.manage_issue_builder") is False

    def test_reviewer_is_not_staff(self):
        issue = make_issue()
        user = UserFactory(email="reviewer3@example.com")
        contributor = make_contributor(issue, "reviewer3@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.is_staff is False

    def test_reviewer_can_recommend_articles(self):
        issue = make_issue()
        user = UserFactory(email="reviewer4@example.com")
        contributor = make_contributor(issue, "reviewer4@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert can_recommend_pubmed_articles(user) is True


# ---------------------------------------------------------------------------
# 3. Coordinator invite grants can_recommend + editorial + is_staff
# ---------------------------------------------------------------------------


class TestCoordinatorInvitePermissions:
    def test_coordinator_gets_can_recommend(self):
        issue = make_issue()
        user = UserFactory(email="coord@example.com")
        contributor = make_contributor(issue, "coord@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.can_recommend") is True

    def test_coordinator_gets_manage_issue_builder(self):
        issue = make_issue()
        user = UserFactory(email="coord2@example.com")
        contributor = make_contributor(issue, "coord2@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.manage_issue_builder") is True

    def test_coordinator_gets_regional_coordinator(self):
        issue = make_issue()
        user = UserFactory(email="coord3@example.com")
        contributor = make_contributor(issue, "coord3@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.regional_coordinator") is True

    def test_coordinator_is_staff(self):
        issue = make_issue()
        user = UserFactory(email="coord4@example.com")
        contributor = make_contributor(issue, "coord4@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.is_staff is True

    def test_coordinator_does_not_get_chief_editor(self):
        issue = make_issue()
        user = UserFactory(email="coord5@example.com")
        contributor = make_contributor(issue, "coord5@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.chief_editor") is False


# ---------------------------------------------------------------------------
# 4. Accepted contributors can access /journals
# ---------------------------------------------------------------------------


class TestAcceptedContributorsAccessJournals:
    def test_reviewer_can_access_journals(self):
        issue = make_issue()
        user = UserFactory(email="jr@example.com")
        contributor = make_contributor(issue, "jr@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        client = Client()
        client.force_login(User.objects.get(pk=user.pk))
        response = client.get(reverse("submissions:journal_list"))
        assert response.status_code == 200

    def test_coordinator_can_access_journals(self):
        issue = make_issue()
        user = UserFactory(email="jc@example.com")
        contributor = make_contributor(issue, "jc@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)

        accept_invite(user, raw_token)

        client = Client()
        client.force_login(User.objects.get(pk=user.pk))
        response = client.get(reverse("submissions:journal_list"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. can_recommend gates the recommend action
# ---------------------------------------------------------------------------


class TestCanRecommendGating:
    def test_anonymous_cannot_recommend(self):
        from django.contrib.auth.models import AnonymousUser

        assert can_recommend_pubmed_articles(AnonymousUser()) is False

    def test_plain_user_can_recommend(self):
        user = UserFactory()
        assert can_recommend_pubmed_articles(user) is True

    def test_user_with_permission_can_recommend(self):
        from django.contrib.auth.models import Permission

        user = UserFactory()
        perm = Permission.objects.get(
            content_type__app_label="submissions",
            codename="can_recommend",
        )
        user.user_permissions.add(perm)
        user = User.objects.get(pk=user.pk)
        assert can_recommend_pubmed_articles(user) is True


# ---------------------------------------------------------------------------
# 6. Subscriber linked to user on login
# ---------------------------------------------------------------------------


class TestSubscriberLinking:
    def test_existing_subscriber_linked_on_login(self):
        """An orphaned Subscriber with matching email gets linked to the user on login."""
        subscriber = Subscriber.objects.create(email="sub@example.com", subscribed=True)
        assert subscriber.user is None

        user = UserFactory(email="sub@example.com")
        client = Client()
        client.force_login(user)

        # force_login doesn't call adapter.login(), so simulate via the adapter directly
        from django.test import RequestFactory

        from spanza_journal_watch.users.adapters import AccountAdapter

        factory = RequestFactory()
        request = factory.get("/")
        request.session = client.session
        AccountAdapter().login(request, user)

        subscriber.refresh_from_db()
        assert subscriber.user == user

    def test_subscriber_case_insensitive_linking(self):
        """Subscriber email matching should be case-insensitive."""
        subscriber = Subscriber.objects.create(email="Sub@Example.COM", subscribed=True)

        user = UserFactory(email="sub@example.com")

        # Directly test the linking query used in the adapter
        Subscriber.objects.filter(email__iexact=user.email, user__isnull=True).update(user=user)

        subscriber.refresh_from_db()
        assert subscriber.user == user

    def test_already_linked_subscriber_not_overwritten(self):
        """A Subscriber already linked to a different user should not be re-linked."""
        other_user = UserFactory(email="other@example.com")
        subscriber = Subscriber.objects.create(email="shared@example.com", subscribed=True, user=other_user)

        user = UserFactory(email="shared@example.com")

        # Directly test the linking query used in the adapter
        Subscriber.objects.filter(email__iexact=user.email, user__isnull=True).update(user=user)

        subscriber.refresh_from_db()
        # Should NOT have changed — user__isnull=True filter means already-linked is skipped
        assert subscriber.user == other_user


# ---------------------------------------------------------------------------
# 7. Chief editor promotion via Users list
# ---------------------------------------------------------------------------


def _make_chief_editor(user):
    """Grant chief editor permissions to a user for test setup."""
    from django.contrib.auth.models import Permission as DjangoPerm

    for app_label, codename in [
        ("submissions", "chief_editor"),
        ("submissions", "manage_issue_builder"),
        ("backend", "manage_subscriber_csv"),
    ]:
        perm = DjangoPerm.objects.get(content_type__app_label=app_label, codename=codename)
        user.user_permissions.add(perm)
    user.is_staff = True
    user.save(update_fields=["is_staff"])


class TestChiefEditorPromotion:
    def test_promote_grants_full_permission_bundle(self):
        """Promoting to chief editor grants all expected permissions."""
        chief = UserFactory(email="chief@example.com")
        _make_chief_editor(chief)

        target = UserFactory(email="target@example.com")
        client = Client()
        client.force_login(User.objects.get(pk=chief.pk))

        url = reverse("backend:user_toggle_chief_editor", kwargs={"user_id": target.pk})
        response = client.post(url)
        assert response.status_code == 302

        target = User.objects.get(pk=target.pk)
        assert target.has_perm("submissions.chief_editor") is True
        assert target.has_perm("submissions.manage_issue_builder") is True
        assert target.has_perm("submissions.can_recommend") is True
        assert target.has_perm("backend.manage_subscriber_csv") is True
        assert target.has_perm("backend.send_newsletters") is True
        assert target.is_staff is True

    def test_demote_removes_only_chief_editor(self):
        """Demoting removes chief_editor but leaves other permissions."""
        chief = UserFactory(email="chief2@example.com")
        _make_chief_editor(chief)

        target = UserFactory(email="target2@example.com")
        _make_chief_editor(target)

        client = Client()
        client.force_login(User.objects.get(pk=chief.pk))

        url = reverse("backend:user_toggle_chief_editor", kwargs={"user_id": target.pk})
        client.post(url)

        target = User.objects.get(pk=target.pk)
        assert target.has_perm("submissions.chief_editor") is False
        # Other permissions should remain
        assert target.has_perm("submissions.manage_issue_builder") is True

    def test_cannot_promote_self(self):
        """A chief editor cannot modify their own chief editor status."""
        chief = UserFactory(email="selfmod@example.com")
        _make_chief_editor(chief)

        client = Client()
        client.force_login(User.objects.get(pk=chief.pk))

        url = reverse("backend:user_toggle_chief_editor", kwargs={"user_id": chief.pk})
        response = client.post(url)
        assert response.status_code == 302  # redirect with error message

        chief = User.objects.get(pk=chief.pk)
        assert chief.has_perm("submissions.chief_editor") is True  # unchanged

    def test_non_chief_cannot_promote(self):
        """Only chief editors can promote other users."""
        non_chief = UserFactory(email="nochief@example.com")
        non_chief.is_staff = True
        non_chief.save()

        target = UserFactory(email="target3@example.com")
        client = Client()
        client.force_login(non_chief)

        url = reverse("backend:user_toggle_chief_editor", kwargs={"user_id": target.pk})
        response = client.post(url)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 8. Coordinator access restrictions
# ---------------------------------------------------------------------------


class TestCoordinatorAccessRestrictions:
    def test_coordinator_sees_only_assigned_issues_on_dashboard(self):
        """Coordinators should only see issues they are assigned to."""
        issue1 = make_issue()
        issue2 = Issue.objects.create(name="Other Issue", active=False)

        user = UserFactory(email="coordash@example.com")
        contributor = make_contributor(issue1, "coordash@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)
        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        client = Client()
        client.force_login(user)

        response = client.get(reverse("backend:dashboard"))
        content = response.content.decode()
        assert issue1.name in content
        assert issue2.name not in content

    def test_coordinator_cannot_access_settings(self):
        """Coordinators should not be able to access chief-editor-only pages."""
        issue = make_issue()
        user = UserFactory(email="coordsettings@example.com")
        contributor = make_contributor(issue, "coordsettings@example.com", IssueContributor.Role.COORDINATOR)
        _, raw_token = make_invite(contributor)
        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        client = Client()
        client.force_login(user)

        response = client.get(reverse("backend:backend_settings"))
        assert response.status_code == 403

    def test_reviewer_cannot_access_backend_dashboard(self):
        """Accepted reviewers should not be able to access the backend dashboard."""
        issue = make_issue()
        user = UserFactory(email="revdash@example.com")
        contributor = make_contributor(issue, "revdash@example.com", IssueContributor.Role.REVIEWER)
        _, raw_token = make_invite(contributor)
        accept_invite(user, raw_token)

        user = User.objects.get(pk=user.pk)
        client = Client()
        client.force_login(user)

        response = client.get(reverse("backend:dashboard"))
        # Reviewer has no backend permissions — should get 403 or redirect
        assert response.status_code in (302, 403)


# ---------------------------------------------------------------------------
# 9. Chief editor invitation flow
# ---------------------------------------------------------------------------


def make_chief_editor_invite(email, name="", created_by=None):
    """Create a ChiefEditorInvite and return (invite, raw_token)."""
    raw_token = ChiefEditorInvite.generate_raw_token()
    token_hash = ChiefEditorInvite.hash_token(raw_token)
    invite = ChiefEditorInvite.objects.create(
        email=email,
        name=name,
        token_hash=token_hash,
        expires_at=timezone.now() + datetime.timedelta(days=180),
        created_by=created_by,
        sent_at=timezone.now(),
    )
    return invite, raw_token


@pytest.mark.django_db
class TestChiefEditorInviteFlow:
    def test_accept_invite_grants_full_permissions(self):
        """Accepting a chief editor invite grants the full permission bundle."""
        user = UserFactory(email="newhire@example.com")
        _, raw_token = make_chief_editor_invite("newhire@example.com")

        client = Client()
        client.force_login(user)
        url = reverse("chief_editor_invite_accept", kwargs={"token": raw_token})
        response = client.get(url)
        assert response.status_code == 200

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.chief_editor") is True
        assert user.has_perm("submissions.manage_issue_builder") is True
        assert user.has_perm("submissions.can_recommend") is True
        assert user.has_perm("backend.manage_subscriber_csv") is True
        assert user.has_perm("backend.send_newsletters") is True
        assert user.is_staff is True

    def test_accept_invite_marks_consumed(self):
        """Accepting an invite marks it as consumed."""
        user = UserFactory(email="consumed@example.com")
        invite, raw_token = make_chief_editor_invite("consumed@example.com")

        client = Client()
        client.force_login(user)
        url = reverse("chief_editor_invite_accept", kwargs={"token": raw_token})
        client.get(url)

        invite.refresh_from_db()
        assert invite.consumed_at is not None
        assert invite.accepted_by == user

    def test_expired_invite_rejected(self):
        """An expired invite should not grant permissions."""
        user = UserFactory(email="expired@example.com")
        invite, raw_token = make_chief_editor_invite("expired@example.com")
        invite.expires_at = timezone.now() - datetime.timedelta(days=1)
        invite.save(update_fields=["expires_at"])

        client = Client()
        client.force_login(user)
        url = reverse("chief_editor_invite_accept", kwargs={"token": raw_token})
        response = client.get(url)
        assert response.status_code == 200

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.chief_editor") is False

    def test_wrong_email_rejected(self):
        """A user with a different email should be signed out."""
        user = UserFactory(email="wrong@example.com")
        _, raw_token = make_chief_editor_invite("correct@example.com")

        client = Client()
        client.force_login(user)
        url = reverse("chief_editor_invite_accept", kwargs={"token": raw_token})
        response = client.get(url)
        # Should redirect (sign out + redirect to same page)
        assert response.status_code == 302

        user = User.objects.get(pk=user.pk)
        assert user.has_perm("submissions.chief_editor") is False

    def test_send_invite_requires_chief_editor(self):
        """Only existing chief editors can send invites."""
        non_chief = UserFactory(email="nobody@example.com")
        non_chief.is_staff = True
        non_chief.save()

        client = Client()
        client.force_login(non_chief)
        url = reverse("backend:send_chief_editor_invite")
        response = client.post(url, {"email": "new@example.com", "name": "New"})
        assert response.status_code == 403
