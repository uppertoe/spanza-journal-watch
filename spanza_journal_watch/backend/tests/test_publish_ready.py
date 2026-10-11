"""Coordinators pull, edit and mark publish ready their own issue's reviews; publishing stays with the chief editor."""

import datetime

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.backend.models import IssueContributor, PlankaIssueBinding, PubmedArticle
from spanza_journal_watch.backend.views.planka_cards import _refresh_binding_lists_from_board
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _client_with(*codenames):
    user = UserFactory()
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename, content_type__app_label="submissions"))
    client = Client()
    client.force_login(user)
    return client, user


def _coordinator_of(issue):
    client, user = _client_with("regional_coordinator", "manage_issue_builder")
    IssueContributor.objects.create(
        issue=issue,
        email=user.email,
        user=user,
        role=IssueContributor.Role.COORDINATOR,
        status=IssueContributor.Status.ACTIVE,
    )
    return client


def _review(issue, title="An article", **fields):
    journal, _ = Journal.objects.get_or_create(name="Test Journal", defaults={"active": True})
    article = PubmedArticle.objects.create(title=title, publication_date=datetime.date(2026, 1, 1), journal=journal)
    author, _ = Author.objects.get_or_create(name="Dr Test", defaults={"title": "Dr"})
    review = Review.objects.create(article=article, author=author, body="A review body.", **fields)
    issue.reviews.add(review)
    return review


@pytest.fixture()
def issue():
    return Issue.objects.create(name="Own Issue", body="Intro", active=False)


@pytest.fixture()
def other_issue():
    return Issue.objects.create(name="Other Issue", body="Intro", active=False)


class TestCoordinatorAccess:
    def test_edit_reviews_opens_for_own_issue_only(self, issue, other_issue):
        client = _coordinator_of(issue)
        _review(issue)
        own = client.get(reverse("backend:issue_reviews_edit") + f"?issue={issue.pk}")
        assert own.status_code == 200
        assert "Mark publish ready" in own.content.decode()
        other = client.get(reverse("backend:issue_reviews_edit") + f"?issue={other_issue.pk}")
        assert other.status_code == 403

    def test_pull_reviews_opens_for_own_issue_only(self, issue, other_issue):
        client = _coordinator_of(issue)
        assert client.get(reverse("backend:issue_planka_import") + f"?issue={issue.pk}").status_code == 200
        assert client.get(reverse("backend:issue_planka_import") + f"?issue={other_issue.pk}").status_code == 403

    def test_import_refused_on_another_issue(self, issue, other_issue):
        client = _coordinator_of(issue)
        PlankaIssueBinding.objects.create(issue=other_issue, project_id="p", board_id="b")
        response = client.post(reverse("backend:planka_import_publish_cards_bulk", args=[other_issue.pk]))
        assert response.status_code == 403

    def test_review_edits_refused_on_another_issue(self, issue, other_issue):
        client = _coordinator_of(issue)
        review = _review(other_issue)
        url = reverse("backend:edit_issue_review_form", args=[other_issue.pk, review.pk])
        assert client.get(url).status_code == 403
        url = reverse("backend:set_review_publish_ready", args=[other_issue.pk, review.pk])
        assert client.post(url, {"ready": "1"}).status_code == 403
        review.refresh_from_db()
        assert review.publish_ready is False

    def test_edit_form_opens_on_own_issue(self, issue):
        client = _coordinator_of(issue)
        review = _review(issue)
        response = client.get(reverse("backend:edit_issue_review_form", args=[issue.pk, review.pk]), **HTMX)
        assert response.status_code == 200
        content = response.content.decode()
        # Chief-editor-only controls are not offered.
        assert "Suggest with Claude" not in content
        assert "Manage MeSH mappings" not in content

    def test_publishing_stays_with_the_chief_editor(self, issue):
        client = _coordinator_of(issue)
        review = _review(issue, publish_ready=True)
        assert client.get(reverse("backend:issue_publish") + f"?issue={issue.pk}").status_code == 403
        assert client.post(reverse("backend:toggle_review_active", args=[review.pk])).status_code == 403
        assert client.post(reverse("backend:publish_issue_bundle", args=[issue.pk])).status_code == 403


class TestMarkingPublishReady:
    def test_mark_and_return_to_editing(self, issue):
        client = _coordinator_of(issue)
        review = _review(issue)
        url = reverse("backend:set_review_publish_ready", args=[issue.pk, review.pk])

        response = client.post(url, {"ready": "1"}, **HTMX)
        review.refresh_from_db()
        assert review.publish_ready is True
        assert ">Unmark<" in response.content.decode()

        client.post(url, {"ready": "0"}, **HTMX)
        review.refresh_from_db()
        assert review.publish_ready is False

    def test_bulk_marks_only_this_issues_selected_reviews(self, issue, other_issue):
        client, _ = _client_with("chief_editor", "manage_issue_builder")
        chosen = _review(issue, title="Chosen")
        left = _review(issue, title="Left alone")
        elsewhere = _review(other_issue, title="Elsewhere")

        client.post(
            reverse("backend:mark_reviews_publish_ready", args=[issue.pk]),
            {"review_ids": [chosen.pk, elsewhere.pk]},
            **HTMX,
        )

        for review in (chosen, left, elsewhere):
            review.refresh_from_db()
        assert chosen.publish_ready is True
        assert left.publish_ready is False
        assert elsewhere.publish_ready is False


class TestPublishWarnings:
    def test_unmarked_reviews_warn_but_do_not_block(self, issue):
        client, _ = _client_with("chief_editor", "manage_issue_builder")
        _review(issue, title="Marked", publish_ready=True)
        _review(issue, title="Unmarked")

        content = client.get(reverse("backend:issue_publish") + f"?issue={issue.pk}").content.decode()

        assert "1 review has not yet been marked publish ready on the Edit Reviews tab." in content
        assert "1 of 2 reviews has not yet been marked publish ready. Make this issue and all 2 reviews" in content
        assert "This review has not yet been marked publish ready. Make this review visible" in content
        assert "Fix the items listed below first" not in content

    def test_no_warning_once_every_review_is_marked(self, issue):
        client, _ = _client_with("chief_editor", "manage_issue_builder")
        _review(issue, publish_ready=True)

        content = client.get(reverse("backend:issue_publish") + f"?issue={issue.pk}").content.decode()

        assert "not yet been marked publish ready" not in content


class _FakeBoardClient:
    def __init__(self, list_names):
        self.lists = [{"id": f"id-{i}", "name": name} for i, name in enumerate(list_names)]

    def get_board(self, board_id):
        return {}, {"lists": self.lists}


class TestBoardListNames:
    @pytest.mark.parametrize("name", ["Ready to submit", "Publish ready"])
    def test_last_list_recognised_under_either_name(self, issue, name):
        binding = PlankaIssueBinding.objects.create(issue=issue, project_id="p", board_id="b", lists={})
        board_client = _FakeBoardClient(["Candidates", "Under review", name])
        _refresh_binding_lists_from_board(client=board_client, binding=binding)
        binding.refresh_from_db()
        assert binding.lists["publish_ready"] == "id-2"
