"""Editorial headlines: extraction from the body, drafting via Claude, editor and page rendering."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.urls import reverse

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions.headlines import draft_review_headline, extract_bottom_line
from spanza_journal_watch.submissions.models import Issue, Review
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class TestExtractBottomLine:
    def test_finds_take_home_section_and_flattens_bullets(self):
        body = (
            "## Summary\n\nA trial of something.\n\n"
            "## Take Home Messages\n\n- Pain follows three trajectories\n- Severity predicts outcome\n\n"
            "## References\n\n1. Foo"
        )
        assert extract_bottom_line(body) == "Pain follows three trajectories. Severity predicts outcome."

    def test_prefers_take_home_over_conclusions_and_strips_markdown(self):
        body = (
            "**Conclusions**\n\nWeak.\n\n**Bottom line:**\n\nStrict *fasting* adds [risk](http://x) without benefit.\n"
        )
        assert extract_bottom_line(body) == "Strict fasting adds risk without benefit."

    def test_returns_empty_when_no_section(self):
        assert extract_bottom_line("## Methods\n\nStuff.\n\n## Results\n\nMore stuff.") == ""
        assert extract_bottom_line("") == ""

    def test_caps_length(self):
        body = "## Take home\n\n" + " ".join(["word"] * 200)
        assert extract_bottom_line(body).endswith("…")
        assert len(extract_bottom_line(body).split()) <= 71


def _fake_client(payload, stop_reason="end_turn"):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )
    client = MagicMock()
    client.beta.messages.create.return_value = response
    return client


def _review(body="## Bottom line\n\nMonitor quantitatively.", headline="", active=True):
    article = PubmedArticle.objects.create(pmid="910001", title="A paper about neuromuscular block")
    return Review.objects.create(article=article, body=body, active=active, editorial_headline=headline)


class TestDraftReviewHeadline:
    def test_returns_cleaned_headline_and_bottom_line(self):
        review = _review()
        client = _fake_client(
            {"headline": "  Quantitative monitoring is the standard.  ", "bottom_line": "Use it.\nEvery time."}
        )
        draft = draft_review_headline(review, client=client)
        assert draft == {"headline": "Quantitative monitoring is the standard", "bottom_line": "Use it. Every time."}
        kwargs = client.beta.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["output_config"]["format"]["type"] == "json_schema"
        assert "Monitor quantitatively." in kwargs["messages"][0]["content"]
        assert "A paper about neuromuscular block" in kwargs["messages"][0]["content"]

    def test_refusal_and_bad_json_return_none(self):
        review = _review()
        assert draft_review_headline(review, client=_fake_client({}, stop_reason="refusal")) is None
        client = MagicMock()
        client.beta.messages.create.return_value = SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")]
        )
        assert draft_review_headline(review, client=client) is None

    def test_no_api_key_returns_none(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        assert draft_review_headline(_review()) is None


class TestCommand:
    def test_drafts_only_reviews_without_headline(self, settings):
        settings.ANTHROPIC_API_KEY = "test"
        missing = _review()
        existing = Review.objects.create(
            article=PubmedArticle.objects.create(pmid="910002", title="Other"),
            body="x",
            active=True,
            editorial_headline="Already written",
        )
        client = _fake_client({"headline": "Drafted headline", "bottom_line": "Drafted bottom line."})
        with patch(
            "spanza_journal_watch.submissions.management.commands.draft_review_headlines.build_client",
            return_value=client,
        ):
            call_command("draft_review_headlines")
        missing.refresh_from_db()
        existing.refresh_from_db()
        # Drafts land in the draft fields; the live headline stays empty until approved.
        assert missing.draft_headline == "Drafted headline"
        assert missing.draft_bottom_line == "Drafted bottom line."
        assert missing.editorial_headline == ""
        assert missing.draft_generated_at is not None
        assert existing.editorial_headline == "Already written"
        assert client.beta.messages.create.call_count == 1

    def test_dry_run_saves_nothing(self, settings):
        settings.ANTHROPIC_API_KEY = "test"
        review = _review()
        client = _fake_client({"headline": "Drafted", "bottom_line": ""})
        with patch(
            "spanza_journal_watch.submissions.management.commands.draft_review_headlines.build_client",
            return_value=client,
        ):
            call_command("draft_review_headlines", dry_run=True)
        review.refresh_from_db()
        assert review.editorial_headline == ""
        assert review.draft_headline == ""


class TestPublicRendering:
    def test_drafts_are_never_rendered(self, client):
        review = _review()
        review.draft_headline = "Unapproved draft headline"
        review.draft_bottom_line = "Unapproved bottom line."
        review.save()
        body = client.get(review.get_absolute_url()).content.decode()
        assert "Unapproved" not in body
        assert "<title>A paper about neuromuscular block | SPANZA Journal Watch</title>" in body

    def test_headline_becomes_title_h1_and_structured_data(self, client):
        review = _review(headline="Children need quantitative monitoring of neuromuscular block")
        review.bottom_line = "Monitor every child. Reverse based on the numbers."
        review.save()
        body = client.get(review.get_absolute_url()).content.decode()
        assert (
            "<title>Children need quantitative monitoring of neuromuscular block | SPANZA Journal Watch</title>"
            in body
        )
        assert "Children need quantitative monitoring of neuromuscular block" in body.split("<article")[0]
        assert '"alternativeHeadline": "A paper about neuromuscular block"' in body
        assert 'name="description" content="Monitor every child. Reverse based on the numbers.' in body
        assert 'aria-label="Bottom line"' in body

    def test_without_headline_falls_back_to_paper_title(self, client):
        review = _review()
        body = client.get(review.get_absolute_url()).content.decode()
        assert "<title>A paper about neuromuscular block | SPANZA Journal Watch</title>" in body
        assert 'aria-label="Bottom line"' not in body


class TestEditor:
    def _editor(self, client):
        user = UserFactory()
        user.user_permissions.add(
            Permission.objects.get(codename="chief_editor"), Permission.objects.get(codename="manage_issue_builder")
        )
        client.force_login(user)
        return Issue.objects.create(name="Editor issue", body="b")

    def test_suggest_fills_fields_from_unsaved_body(self, client, settings):
        settings.ANTHROPIC_API_KEY = "test"
        issue = self._editor(client)
        with patch(
            "spanza_journal_watch.submissions.headlines.build_client",
            return_value=_fake_client({"headline": "Suggested headline", "bottom_line": "Suggested line."}),
        ):
            response = client.post(
                reverse("backend:suggest_review_headline", kwargs={"issue_id": issue.pk}),
                {"body": "## Bottom line\n\nSomething.", "article_name": "A paper"},
            )
        html = response.content.decode()
        assert response.status_code == 200
        assert 'value="Suggested headline"' in html
        assert "Suggested line." in html
        assert "Drafted from the review text" in html

    def test_suggest_without_body_explains(self, client):
        issue = self._editor(client)
        response = client.post(reverse("backend:suggest_review_headline", kwargs={"issue_id": issue.pk}), {"body": ""})
        assert "write the review body first" in response.content.decode()

    def test_review_form_saves_headline(self):
        from spanza_journal_watch.backend.forms import IssueBuilderReviewForm
        from spanza_journal_watch.submissions.models import Author

        issue = Issue.objects.create(name="Form issue", body="b")
        review = _review()
        author = Author.objects.create(name="Dr Form")
        form = IssueBuilderReviewForm(
            {
                "article_mode": "existing",
                "existing_article": review.article.pk,
                "author_mode": "existing",
                "author": author.pk,
                "body": "Body text",
                "headline": "  A written headline ",
                "bottom_line": "Line.",
            },
            issue=issue,
            review=review,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.editorial_headline == "A written headline"
        assert saved.bottom_line == "Line."


class TestHeadlineQueue:
    def _chief(self, client):
        user = UserFactory()
        user.user_permissions.add(
            Permission.objects.get(codename="chief_editor"), Permission.objects.get(codename="manage_issue_builder")
        )
        client.force_login(user)
        return user

    def test_queue_lists_by_status(self, client):
        self._chief(client)
        missing = _review()
        drafted = Review.objects.create(
            article=PubmedArticle.objects.create(pmid="910010", title="Drafted paper"),
            body="x",
            active=True,
            draft_headline="A draft",
        )
        done = Review.objects.create(
            article=PubmedArticle.objects.create(pmid="910011", title="Done paper"),
            body="x",
            active=True,
            editorial_headline="Done headline",
        )
        Review.objects.create(
            article=PubmedArticle.objects.create(pmid="910012", title="Inactive paper"), body="x", active=False
        )

        page = client.get(reverse("backend:headline_queue")).content.decode()
        assert "A paper about neuromuscular block" in page and "Drafted paper" not in page
        assert "Needs headline" in page and "Monitor quantitatively." in page  # take-home shown for reference
        # The full review text is available beside the fields, rendered from markdown.
        assert f'id="review-text-{missing.pk}"' in page
        assert "Show review text" in page
        assert "<h2>Bottom line</h2>" in page or "Bottom line</h2>" in page

        page = client.get(reverse("backend:headline_queue") + "?status=draft").content.decode()
        assert "Drafted paper" in page and 'value="A draft"' in page

        page = client.get(reverse("backend:headline_queue") + "?status=done").content.decode()
        assert "Done paper" in page and 'value="Done headline"' in page
        assert "Inactive paper" not in page
        assert missing.pk and done.pk and drafted.pk

    def test_save_approves_and_clears_draft_and_pings_indexnow(self, client):
        self._chief(client)
        review = _review()
        review.draft_headline = "Draft to approve"
        review.draft_bottom_line = "Draft line."
        review.save()
        with patch("spanza_journal_watch.backend.views.shared.queue_indexnow_submission") as queue:
            with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
                response = client.post(
                    reverse("backend:headline_save", kwargs={"review_id": review.pk}),
                    {"headline": "  Edited   headline ", "bottom_line": "Edited line."},
                    HTTP_HX_REQUEST="true",
                )
        assert response.status_code == 200
        review.refresh_from_db()
        assert review.editorial_headline == "Edited headline"
        assert review.bottom_line == "Edited line."
        assert review.draft_headline == "" and review.draft_bottom_line == ""
        assert review.get_absolute_url() in queue.call_args[0][0]
        assert "Done" in response.content.decode()

    def test_draft_button_fills_row(self, client, settings):
        settings.ANTHROPIC_API_KEY = "test"
        self._chief(client)
        review = _review()
        with patch(
            "spanza_journal_watch.submissions.headlines.build_client",
            return_value=_fake_client({"headline": "Machine draft", "bottom_line": "Machine line."}),
        ):
            response = client.post(
                reverse("backend:headline_draft", kwargs={"review_id": review.pk}), HTTP_HX_REQUEST="true"
            )
        html = response.content.decode()
        assert 'value="Machine draft"' in html and "Draft waiting" in html
        review.refresh_from_db()
        assert review.draft_headline == "Machine draft"
        assert review.editorial_headline == ""

    def test_draft_all_queues_task(self, client, settings):
        settings.ANTHROPIC_API_KEY = "test"
        self._chief(client)
        _review()
        with patch("spanza_journal_watch.submissions.tasks.draft_missing_headlines_task.delay") as delay:
            response = client.post(reverse("backend:headline_draft_all"))
        assert response.status_code == 302
        delay.assert_called_once()

    def test_queue_requires_chief_editor(self, client):
        client.force_login(UserFactory())
        assert client.get(reverse("backend:headline_queue")).status_code == 403
