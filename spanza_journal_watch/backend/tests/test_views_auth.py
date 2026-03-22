"""
Auth / permission guard tests for the backend views.

For every protected view, verifies:
  1. Unauthenticated → 302 redirect containing /accounts/login/
  2. Authenticated without the required permission → 403

Views tested:
  - Subscriber CSV management (manage_subscriber_csv)
  - Dashboard / backend_go (login_required only)
  - PubMed article intake (manage_issue_builder)
  - Watched journals (manage_issue_builder)
  - Issue builder — manage_issue_builder views
  - Issue builder — chief_editor-only views
  - Contributors (manage_issue_builder)
  - Newsletter (send_newsletters / view_newsletter_stats)
  - Settings / Planka management (chief_editor)
  - Authors / affiliations (manage_issue_builder)
  - Planka card revisions (manage_issue_builder)
"""

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import (
    IssueContributor,
    PlankaCardRevision,
    PlankaIssueBinding,
    WatchedJournal,
)
from spanza_journal_watch.submissions.models import Article, Author, Issue, Review
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

MANAGE_ISSUE_BUILDER = "submissions.manage_issue_builder"
CHIEF_EDITOR = "submissions.chief_editor"
MANAGE_CSV = "backend.manage_subscriber_csv"
SEND_NEWSLETTERS = "backend.send_newsletters"
VIEW_NEWSLETTER_STATS = "backend.view_newsletter_stats"


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)
    return user


def anon():
    return Client()


def user_without_perm():
    u = UserFactory()
    c = Client()
    c.force_login(u)
    return c


def user_with_perm(*perms):
    u = UserFactory()
    _grant(u, *perms)
    c = Client()
    c.force_login(u)
    return c


def _redirect_to_login(response):
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def _forbidden(response):
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Fixtures for URL kwargs
# ---------------------------------------------------------------------------


@pytest.fixture()
def issue():
    return Issue.objects.create(name="Test Issue", active=False)


@pytest.fixture()
def binding(issue):
    return PlankaIssueBinding.objects.create(issue=issue, project_id="p1", project_name="P", board_id="b1")


@pytest.fixture()
def contributor(issue):
    return IssueContributor.objects.create(issue=issue, email="r@example.com")


@pytest.fixture()
def watched_journal():
    return WatchedJournal.objects.create(name="Test Journal")


@pytest.fixture()
def author():
    return Author.objects.create(name="Dr Test")


@pytest.fixture()
def article():
    return Article.objects.create(name="Test Article", year=2024)


@pytest.fixture()
def review(article, author, issue):
    r = Review.objects.create(article=article, author=author, body="body")
    issue.reviews.add(r)
    return r


@pytest.fixture()
def revision(binding):
    rev, _ = PlankaCardRevision.record(
        binding=binding,
        card_id="card-1",
        card_name="Card",
        board_id="b1",
        description="text",
    )
    return rev


# ---------------------------------------------------------------------------
# Helper: build a guard table and run checks
# ---------------------------------------------------------------------------


def check_guards(url, *, method="get", data=None):
    """Assert that the URL requires login and non-permissioned users get 403."""
    anon_resp = getattr(anon(), method)(url, data or {})
    _redirect_to_login(anon_resp)

    no_perm_resp = getattr(user_without_perm(), method)(url, data or {})
    _forbidden(no_perm_resp)


# ---------------------------------------------------------------------------
# 1. Subscriber CSV (manage_subscriber_csv)
# ---------------------------------------------------------------------------


class TestSubscriberCSVAuthGuards:
    def test_upload_subscriber_csv(self):
        url = reverse("backend:upload_subscribers")
        check_guards(url)

    def test_subscriber_list(self):
        url = reverse("backend:subscriber_list")
        check_guards(url)


# ---------------------------------------------------------------------------
# 2. Dashboard / backend_go (login_required, no specific permission)
# ---------------------------------------------------------------------------


class TestDashboardAuthGuards:
    def test_backend_go_redirects_unauthenticated(self):
        url = reverse("backend:backend_go")
        _redirect_to_login(anon().get(url))

    def test_dashboard_redirects_unauthenticated(self):
        url = reverse("backend:dashboard")
        _redirect_to_login(anon().get(url))


# ---------------------------------------------------------------------------
# 3. PubMed article intake (manage_issue_builder)
# ---------------------------------------------------------------------------


class TestArticleIntakeAuthGuards:
    def test_article_intake(self):
        check_guards(reverse("backend:article_intake"))

    def test_watched_journals(self):
        check_guards(reverse("backend:watched_journals"))

    def test_watched_journal_search(self):
        check_guards(reverse("backend:watched_journal_search"))

    def test_watched_journal_toggle_active(self, watched_journal):
        url = reverse("backend:watched_journal_toggle_active", kwargs={"watched_journal_id": watched_journal.pk})
        check_guards(url, method="post")


# ---------------------------------------------------------------------------
# 4. Issue builder — manage_issue_builder views
# ---------------------------------------------------------------------------


class TestIssueBuilderManageGuards:
    def test_issue_builder_get(self):
        check_guards(reverse("backend:issue_builder"))

    def test_issue_reviewers_get(self, issue):
        check_guards(reverse("backend:issue_reviewers"))

    def test_save_issue_draft_post(self, issue):
        url = reverse("backend:update_issue_draft", kwargs={"issue_id": issue.pk})
        check_guards(url, method="post")

    def test_issue_add_contributor(self, issue):
        url = reverse("backend:issue_add_contributor", kwargs={"issue_id": issue.pk})
        check_guards(url, method="post")

    def test_issue_send_contributor_invites(self, issue):
        url = reverse("backend:issue_send_contributor_invites", kwargs={"issue_id": issue.pk})
        check_guards(url, method="post")

    def test_issue_resend_contributor_invite(self, issue, contributor):
        url = reverse(
            "backend:issue_resend_contributor_invite",
            kwargs={"issue_id": issue.pk, "contributor_id": contributor.pk},
        )
        check_guards(url, method="post")

    def test_issue_revoke_contributor(self, issue, contributor):
        url = reverse(
            "backend:issue_revoke_contributor",
            kwargs={"issue_id": issue.pk, "contributor_id": contributor.pk},
        )
        check_guards(url, method="post")

    def test_contributor_author_lookup(self):
        check_guards(reverse("backend:contributor_author_lookup"))

    def test_planka_card_revisions(self, issue, binding):
        url = reverse("backend:planka_card_revisions", kwargs={"issue_id": issue.pk, "card_id": "card-1"})
        check_guards(url)

    def test_planka_card_revision_restore(self, issue, binding, revision):
        url = reverse(
            "backend:planka_card_revision_restore",
            kwargs={"issue_id": issue.pk, "revision_id": revision.pk},
        )
        check_guards(url, method="post")

    def test_affiliations_list(self):
        check_guards(reverse("backend:affiliations_list"))

    def test_authors_list(self):
        check_guards(reverse("backend:authors_list"))


# ---------------------------------------------------------------------------
# 5. Issue builder — chief_editor-only views
# ---------------------------------------------------------------------------


class TestChiefEditorOnlyGuards:
    def test_new_review_form(self, issue):
        url = reverse("backend:new_issue_review_form", kwargs={"issue_id": issue.pk})
        check_guards(url)

    def test_add_issue_review(self, issue):
        url = reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk})
        check_guards(url, method="post")

    def test_edit_issue_review_form(self, issue, review):
        url = reverse(
            "backend:edit_issue_review_form",
            kwargs={"issue_id": issue.pk, "review_id": review.pk},
        )
        check_guards(url)

    def test_update_issue_review(self, issue, review):
        url = reverse(
            "backend:update_issue_review",
            kwargs={"issue_id": issue.pk, "review_id": review.pk},
        )
        check_guards(url, method="post")

    def test_remove_issue_review(self, issue, review):
        url = reverse(
            "backend:remove_issue_review",
            kwargs={"issue_id": issue.pk, "review_id": review.pk},
        )
        check_guards(url, method="post")

    def test_issue_reviews_edit(self):
        check_guards(reverse("backend:issue_reviews_edit"))

    def test_issue_publish(self):
        check_guards(reverse("backend:issue_publish"))

    def test_issue_set_homepage(self):
        check_guards(reverse("backend:issue_set_homepage"), method="post")

    def test_toggle_review_active(self, review):
        url = reverse("backend:toggle_review_active", kwargs={"review_id": review.pk})
        check_guards(url, method="post")

    def test_backend_settings(self):
        check_guards(reverse("backend:backend_settings"))

    def test_planka_run_setup_oidc(self):
        check_guards(reverse("backend:planka_run_setup_oidc"), method="post")

    def test_planka_run_setup_api_key(self):
        check_guards(reverse("backend:planka_run_setup_api_key"), method="post")

    def test_planka_promote_chief_editor(self):
        check_guards(reverse("backend:planka_promote_chief_editor"), method="post")


# ---------------------------------------------------------------------------
# 6. Newsletter — permission guards
# ---------------------------------------------------------------------------


class TestNewsletterAuthGuards:
    def test_newsletter_release_list(self):
        url = reverse("backend:newsletter_release_list")
        _redirect_to_login(anon().get(url))
        _forbidden(user_without_perm().get(url))

    def test_create_newsletter(self):
        # @login_required + @permission_required: anonymous → 302 redirect, no-perm → 403
        url = reverse("backend:create_newsletter")
        _redirect_to_login(anon().get(url))
        _forbidden(user_without_perm().get(url))

    def test_newsletter_stats_list(self):
        # @login_required + @permission_required: anonymous → 302 redirect, no-perm → 403
        url = reverse("backend:newsletter_stats_list")
        _redirect_to_login(anon().get(url))
        _forbidden(user_without_perm().get(url))


# ---------------------------------------------------------------------------
# 7. Verify permissioned users ARE admitted (spot-check)
# ---------------------------------------------------------------------------


class TestPermissionedUserAdmitted:
    def test_manage_issue_builder_user_reaches_watched_journals(self):
        client = user_with_perm(MANAGE_ISSUE_BUILDER)
        response = client.get(reverse("backend:watched_journals"))
        assert response.status_code == 200

    def test_manage_csv_user_reaches_subscriber_list(self):
        client = user_with_perm(MANAGE_CSV)
        response = client.get(reverse("backend:subscriber_list"))
        assert response.status_code == 200

    def test_chief_editor_user_reaches_issue_reviews_edit(self, issue):
        client = user_with_perm(CHIEF_EDITOR, MANAGE_ISSUE_BUILDER)
        response = client.get(reverse("backend:issue_reviews_edit"))
        assert response.status_code == 200
