"""
Tests for the selected_issue context processor.

Covers:
1. Unauthenticated request → empty dict
2. Authenticated user gets issues_for_sidebar with all issues
3. Coordinator-only user gets only their assigned issues
4. Chief editor sees all issues (including unassigned)
5. is_htmx detection via HX-Request header
6. is_coordinator_only flag
7. planka_url injected from settings
8. session_selected_issue resolved correctly
9. Stale session_selected_issue cleared when issue is inaccessible
"""

import pytest
from django.contrib.auth.models import Permission
from django.test import RequestFactory

from spanza_journal_watch.backend.context_processors import selected_issue
from spanza_journal_watch.backend.models import IssueContributor
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def make_issue(name="Issue"):
    return Issue.objects.create(name=name, active=False)


def make_request(user=None, *, htmx=False, session=None):
    factory = RequestFactory()
    request = factory.get("/editorial/")
    request.user = user
    if htmx:
        request.META["HTTP_HX_REQUEST"] = "true"
        # RequestFactory stores headers differently; use META
    # Simulate HX-Request header via META
    if htmx:
        request.META["HTTP_HX_REQUEST"] = "true"
    request.session = session or {}
    request.resolver_match = None
    return request


# ---------------------------------------------------------------------------
# 1. Unauthenticated
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    def test_returns_empty_dict_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        factory = RequestFactory()
        request = factory.get("/")
        request.user = AnonymousUser()
        request.session = {}
        result = selected_issue(request)
        assert result == {}


# ---------------------------------------------------------------------------
# 2. issues_for_sidebar
# ---------------------------------------------------------------------------


class TestIssuesForSidebar:
    def test_all_issues_returned_for_chief_editor(self):
        issue_a = make_issue("A")
        issue_b = make_issue("B")
        user = UserFactory()
        _grant(user, "submissions.chief_editor", "submissions.manage_issue_builder")

        request = make_request(user)
        ctx = selected_issue(request)
        pks = {i.pk for i in ctx["issues_for_sidebar"]}
        assert issue_a.pk in pks
        assert issue_b.pk in pks

    def test_coordinator_only_sees_assigned_issues(self):
        assigned = make_issue("Assigned")
        unassigned = make_issue("Unassigned")
        user = UserFactory()
        _grant(user, "submissions.regional_coordinator")

        IssueContributor.objects.create(
            issue=assigned,
            user=user,
            email=user.email,
            role=IssueContributor.Role.COORDINATOR,
            status=IssueContributor.Status.ACTIVE,
        )

        request = make_request(user)
        ctx = selected_issue(request)
        pks = {i.pk for i in ctx["issues_for_sidebar"]}
        assert assigned.pk in pks
        assert unassigned.pk not in pks

    def test_coordinator_with_chief_editor_sees_all(self):
        """If user has both coordinator and chief_editor, they are NOT coordinator-only."""
        issue_a = make_issue("A")
        issue_b = make_issue("B")
        user = UserFactory()
        _grant(user, "submissions.regional_coordinator", "submissions.chief_editor")

        request = make_request(user)
        ctx = selected_issue(request)
        pks = {i.pk for i in ctx["issues_for_sidebar"]}
        assert issue_a.pk in pks
        assert issue_b.pk in pks


# ---------------------------------------------------------------------------
# 3. is_coordinator_only flag
# ---------------------------------------------------------------------------


class TestIsCoordinatorOnly:
    def test_true_for_coordinator_without_chief_editor(self):
        user = UserFactory()
        _grant(user, "submissions.regional_coordinator")
        ctx = selected_issue(make_request(user))
        assert ctx["is_coordinator_only"] is True

    def test_false_for_coordinator_with_chief_editor(self):
        user = UserFactory()
        _grant(user, "submissions.regional_coordinator", "submissions.chief_editor")
        ctx = selected_issue(make_request(user))
        assert ctx["is_coordinator_only"] is False

    def test_false_for_regular_user_with_no_perms(self):
        user = UserFactory()
        ctx = selected_issue(make_request(user))
        assert ctx["is_coordinator_only"] is False


# ---------------------------------------------------------------------------
# 4. is_htmx detection
# ---------------------------------------------------------------------------


class TestIsHtmx:
    def test_is_htmx_false_without_header(self):
        user = UserFactory()
        request = make_request(user)
        ctx = selected_issue(request)
        assert ctx["is_htmx"] is False

    def test_is_htmx_true_with_header(self):
        user = UserFactory()
        factory = RequestFactory()
        request = factory.get("/", HTTP_HX_REQUEST="true")
        request.user = user
        request.session = {}
        request.resolver_match = None
        ctx = selected_issue(request)
        assert ctx["is_htmx"] is True


# ---------------------------------------------------------------------------
# 5. planka_url
# ---------------------------------------------------------------------------


class TestPlankaUrl:
    def test_planka_url_from_settings(self, settings):
        settings.PLANKA_EXTERNAL_URL = "https://planka.example.com"
        user = UserFactory()
        ctx = selected_issue(make_request(user))
        assert ctx["planka_url"] == "https://planka.example.com"

    def test_planka_url_falls_back_to_base_url(self, settings):
        settings.PLANKA_EXTERNAL_URL = ""
        settings.PLANKA_BASE_URL = "http://planka:1337"
        user = UserFactory()
        ctx = selected_issue(make_request(user))
        assert ctx["planka_url"] == "http://planka:1337"


# ---------------------------------------------------------------------------
# 6. session_selected_issue
# ---------------------------------------------------------------------------


class TestSessionSelectedIssue:
    def test_session_issue_resolved_correctly(self):
        issue = make_issue("Session Issue")
        user = UserFactory()
        _grant(user, "submissions.manage_issue_builder")

        request = make_request(user, session={"selected_issue_id": issue.pk})
        ctx = selected_issue(request)
        assert ctx.get("session_selected_issue") is not None
        assert ctx["session_selected_issue"].pk == issue.pk

    def test_stale_session_issue_id_cleared(self):
        """If the stored issue pk is not in the accessible list, it's removed from session."""
        user = UserFactory()
        # No issues exist → session_selected_issue_id is stale
        session = {"selected_issue_id": 99999}

        request = make_request(user, session=session)
        selected_issue(request)
        # The stale key should be gone
        assert "selected_issue_id" not in request.session

    def test_no_session_issue_key_no_entry_in_context(self):
        user = UserFactory()
        request = make_request(user, session={})
        ctx = selected_issue(request)
        assert "session_selected_issue" not in ctx

    def test_coordinator_only_cannot_see_unassigned_session_issue(self):
        """Coordinator without assignment to the session issue should have it cleared."""
        unassigned_issue = make_issue("Unassigned")
        user = UserFactory()
        _grant(user, "submissions.regional_coordinator")
        session = {"selected_issue_id": unassigned_issue.pk}

        request = make_request(user, session=session)
        ctx = selected_issue(request)
        assert "session_selected_issue" not in ctx
        assert "selected_issue_id" not in request.session
