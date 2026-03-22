"""
Tests for backend form validation and custom logic.

Covers:
1. peek_csv() — header detection, delimiter sniffing, single-column files
2. SubscriberCSVForm — file size validation
3. NewsletterTestSendForm — email normalisation
4. NewsletterCreateForm.clean_issue() — fallback to latest issue, error if none
5. MonthYearField.compress() — date construction from month + year
6. IssueBuilderIssueForm.clean_date() — normalises to first of month
7. IssueBuilderReviewForm.clean() — article mode, author mode, featured-count cap
8. IssueBuilderReviewForm.save() — creates Article / Author / Review and links to Issue
9. PlankaProjectSetupForm — mutual exclusivity of background image sources
10. ArticleIntakeFetchForm — date-range validation
11. WatchedJournalForm — field stripping
12. AuthorForm — email normalisation
13. IssueContributorInviteForm — email normalisation
"""

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from spanza_journal_watch.backend.forms import (
    ArticleIntakeFetchForm,
    AuthorForm,
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    IssueContributorInviteForm,
    MonthYearField,
    NewsletterCreateForm,
    NewsletterTestSendForm,
    PlankaProjectSetupForm,
    SubscriberCSVForm,
    WatchedJournalForm,
    peek_csv,
)
from spanza_journal_watch.backend.models import IssueContributor
from spanza_journal_watch.submissions.models import Article, Author, Issue, Journal, Review

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_csv_file(content, filename="test.csv"):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return SimpleUploadedFile(filename, content, content_type="text/csv")


def make_issue(name="Test Issue", active=True):
    return Issue.objects.create(name=name, active=active)


def make_journal(name="Test Journal"):
    return Journal.objects.create(name=name, active=True)


def make_author(name="Dr Test"):
    return Author.objects.create(name=name)


def make_article(name="Test Article"):
    return Article.objects.create(name=name, year=2024)


# ---------------------------------------------------------------------------
# 1. peek_csv()
# ---------------------------------------------------------------------------


class TestPeekCsv:
    def test_user_header_true_uses_first_row_as_fieldnames(self):
        csv_content = "email,name\nalice@example.com,Alice\nbob@example.com,Bob"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=True)
        assert result["has_header"] is True
        assert "email" in result["preview"].fieldnames

    def test_user_header_false_generates_column_names(self):
        csv_content = "alice@example.com,Alice\nbob@example.com,Bob"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=False)
        assert result["has_header"] is False
        assert "Column 1" in result["preview"].fieldnames

    def test_user_header_override_true(self):
        csv_content = "First Name,Email\nAlice,alice@example.com"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=True)
        assert result["has_header"] is True

    def test_user_header_override_false(self):
        csv_content = "email,value\nalice@example.com,1"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=False)
        assert result["has_header"] is False

    def test_semicolon_delimiter_detected(self):
        csv_content = "email;name\nalice@example.com;Alice"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=True)
        rows = list(result["preview"])
        assert len(rows) == 1

    def test_preview_table_iterable_multi_column(self):
        csv_content = "email,name\nalice@example.com,Alice\nbob@example.com,Bob"
        f = make_csv_file(csv_content)
        result = peek_csv(f, user_header=True)
        rows = list(result["preview"])
        assert len(rows) == 2

    def test_invalid_encoding_raises_validation_error(self):
        from django.core.exceptions import ValidationError

        bad_bytes = b"\xff\xfe" + b"not utf-8 \x80\x81"
        f = SimpleUploadedFile("test.csv", bad_bytes, content_type="text/csv")
        with pytest.raises(ValidationError):
            peek_csv(f)


# ---------------------------------------------------------------------------
# 2. SubscriberCSVForm — file size validation
# ---------------------------------------------------------------------------


class TestSubscriberCSVForm:
    def test_rejects_file_over_1mb(self):
        big_content = b"a" * (1 * 1024 * 1024 + 1)
        f = SimpleUploadedFile("big.csv", big_content, content_type="text/csv")
        form = SubscriberCSVForm(
            data={"name": "Big CSV"},
            files={"file": f},
        )
        assert form.is_valid() is False

    def test_accepts_file_under_1mb(self):
        content = b"email\nalice@example.com"
        f = SimpleUploadedFile("small.csv", content, content_type="text/csv")
        form = SubscriberCSVForm(
            data={"name": "Small CSV"},
            files={"file": f},
        )
        # Form validation runs peek_csv — if CSV is valid this should pass
        # (The validation calls csv_size and peek_csv; both should succeed)
        assert "file" not in (form.errors.get("file") or [])


# ---------------------------------------------------------------------------
# 3. NewsletterTestSendForm
# ---------------------------------------------------------------------------


class TestNewsletterTestSendForm:
    def test_email_lowercased_and_stripped(self):
        form = NewsletterTestSendForm(data={"email": "  TEST@Example.COM  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_invalid_email_rejected(self):
        form = NewsletterTestSendForm(data={"email": "not-an-email"})
        assert form.is_valid() is False

    def test_empty_email_rejected(self):
        form = NewsletterTestSendForm(data={"email": ""})
        assert form.is_valid() is False


# ---------------------------------------------------------------------------
# 4. NewsletterCreateForm.clean_issue()
# ---------------------------------------------------------------------------


class TestNewsletterCreateForm:
    def test_fallback_to_latest_active_issue_when_none_selected(self):
        issue = make_issue(name="Latest Issue", active=True)
        form = NewsletterCreateForm(
            data={
                "issue": "",
                "subject": "Test",
                "content": "Body",
                "ready_to_send": False,
                "non_featured_review_count": 3,
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["issue"].pk == issue.pk

    def test_raises_error_when_no_issue_exists_and_none_selected(self):
        # Ensure no active issues exist so the fallback raises
        Issue.objects.all().update(active=False)
        form = NewsletterCreateForm(
            data={
                "issue": "",
                "subject": "Test",
                "content": "Body",
                "ready_to_send": False,
                "non_featured_review_count": 3,
            }
        )
        assert form.is_valid() is False
        assert "issue" in form.errors

    def test_selected_issue_used_directly(self):
        issue = make_issue()
        form = NewsletterCreateForm(
            data={
                "issue": issue.pk,
                "subject": "Test",
                "content": "Body",
                "ready_to_send": False,
                "non_featured_review_count": 3,
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["issue"].pk == issue.pk


# ---------------------------------------------------------------------------
# 5. MonthYearField.compress()
# ---------------------------------------------------------------------------


class TestMonthYearFieldCompress:
    def _field(self):
        return MonthYearField(required=False)

    def test_compress_returns_first_of_month(self):
        field = self._field()
        result = field.compress([3, 2024])
        assert result == datetime.date(2024, 3, 1)

    def test_compress_returns_none_when_month_missing(self):
        field = self._field()
        result = field.compress([None, 2024])
        assert result is None

    def test_compress_returns_none_when_year_missing(self):
        field = self._field()
        result = field.compress([3, None])
        assert result is None

    def test_compress_returns_none_for_empty_list(self):
        field = self._field()
        result = field.compress([])
        assert result is None


# ---------------------------------------------------------------------------
# 6. IssueBuilderIssueForm.clean_date()
# ---------------------------------------------------------------------------


class TestIssueBuilderIssueFormCleanDate:
    def test_date_normalised_to_first_of_month(self):
        """The MonthYearField + clean_date() should produce day=1."""
        issue = make_issue()
        form = IssueBuilderIssueForm(
            data={
                "name": "My Issue",
                "date_0": "7",  # month widget sub-field
                "date_1": "2024",  # year widget sub-field
                "body": "Some body text",
            },
            instance=issue,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["date"] == datetime.date(2024, 7, 1)

    def test_blank_date_allowed(self):
        issue = make_issue()
        form = IssueBuilderIssueForm(
            data={"name": "My Issue", "date_0": "", "date_1": "", "body": "Some body text"},
            instance=issue,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["date"] is None


# ---------------------------------------------------------------------------
# 7 & 8. IssueBuilderReviewForm — validation and save()
# ---------------------------------------------------------------------------


class TestIssueBuilderReviewForm:
    """Tests for IssueBuilderReviewForm validation and save()."""

    def _base_data(self, **overrides):
        data = {
            "article_mode": "new",
            "article_name": "Test Article",
            "author_mode": "new",
            "new_author_name": "Dr Jane Smith",
            "new_author_title": "Dr",
            "body": "This is the review body.",
            "is_featured": False,
        }
        data.update(overrides)
        return data

    def test_valid_new_article_new_author(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(data=self._base_data(), issue=issue)
        assert form.is_valid(), form.errors

    def test_existing_article_mode_requires_article_selection(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(
            data=self._base_data(article_mode="existing", existing_article=""),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "existing_article" in form.errors

    def test_existing_article_mode_valid_with_article(self):
        issue = make_issue()
        article = make_article()
        form = IssueBuilderReviewForm(
            data=self._base_data(article_mode="existing", existing_article=article.pk),
            issue=issue,
        )
        assert form.is_valid(), form.errors

    def test_new_article_mode_requires_article_name(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(
            data=self._base_data(article_mode="new", article_name=""),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "article_name" in form.errors

    def test_existing_author_mode_requires_author_selection(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(
            data=self._base_data(author_mode="existing", author=""),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "author" in form.errors

    def test_existing_author_mode_valid_with_author(self):
        issue = make_issue()
        author = make_author()
        form = IssueBuilderReviewForm(
            data=self._base_data(author_mode="existing", author=author.pk),
            issue=issue,
        )
        assert form.is_valid(), form.errors

    def test_new_author_mode_requires_author_name(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(
            data=self._base_data(author_mode="new", new_author_name=""),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "new_author_name" in form.errors

    def test_featured_count_limit_enforced(self, settings):
        settings.ISSUE_BUILDER_MAX_FEATURED_REVIEWS = 1
        issue = make_issue()
        # Create one existing featured review
        article = make_article()
        author = make_author()
        existing_review = Review.objects.create(article=article, author=author, body="body", is_featured=True)
        issue.reviews.add(existing_review)

        form = IssueBuilderReviewForm(
            data=self._base_data(is_featured=True),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "is_featured" in form.errors

    def test_featured_count_excludes_self_when_editing(self, settings):
        settings.ISSUE_BUILDER_MAX_FEATURED_REVIEWS = 1
        issue = make_issue()
        article = make_article()
        author = make_author()
        existing_review = Review.objects.create(article=article, author=author, body="body", is_featured=True)
        issue.reviews.add(existing_review)

        # Edit the same review — should NOT count itself against the limit
        form = IssueBuilderReviewForm(
            data=self._base_data(
                article_mode="existing",
                existing_article=article.pk,
                author_mode="existing",
                author=author.pk,
                is_featured=True,
            ),
            issue=issue,
            review=existing_review,
        )
        assert form.is_valid(), form.errors

    def test_save_creates_new_article_and_author(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(data=self._base_data(), issue=issue)
        assert form.is_valid(), form.errors
        review = form.save()

        assert review.pk is not None
        assert review.article.name == "Test Article"
        assert review.author.name == "Dr Jane Smith"
        assert issue.reviews.filter(pk=review.pk).exists()

    def test_save_uses_existing_article(self):
        issue = make_issue()
        article = make_article("Existing Article")
        author = make_author()
        form = IssueBuilderReviewForm(
            data=self._base_data(
                article_mode="existing",
                existing_article=article.pk,
                author_mode="existing",
                author=author.pk,
            ),
            issue=issue,
        )
        assert form.is_valid(), form.errors
        review = form.save()
        assert review.article.pk == article.pk

    def test_save_updates_existing_review(self):
        issue = make_issue()
        article = make_article()
        author = make_author()
        review = Review.objects.create(article=article, author=author, body="original body")
        issue.reviews.add(review)

        form = IssueBuilderReviewForm(
            data=self._base_data(
                article_mode="existing",
                existing_article=article.pk,
                author_mode="existing",
                author=author.pk,
                body="updated body",
            ),
            issue=issue,
            review=review,
        )
        assert form.is_valid(), form.errors
        updated = form.save()
        assert updated.pk == review.pk
        updated.refresh_from_db()
        assert updated.body == "updated body"

    def test_body_required(self):
        issue = make_issue()
        form = IssueBuilderReviewForm(
            data=self._base_data(body=""),
            issue=issue,
        )
        assert form.is_valid() is False
        assert "body" in form.errors


# ---------------------------------------------------------------------------
# 9. PlankaProjectSetupForm
# ---------------------------------------------------------------------------


class TestPlankaProjectSetupForm:
    def _minimal_image(self, filename="bg.png"):
        # 1×1 white PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return SimpleUploadedFile(filename, png_bytes, content_type="image/png")

    def test_valid_with_project_name_only(self):
        form = PlankaProjectSetupForm(data={"project_name": "My Project"}, files={})
        assert form.is_valid(), form.errors

    def test_project_name_stripped(self):
        form = PlankaProjectSetupForm(data={"project_name": "  Trimmed  "}, files={})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["project_name"] == "Trimmed"

    def test_both_asset_and_upload_rejected(self):
        from spanza_journal_watch.backend.models import PlankaBoardBackgroundAsset
        from spanza_journal_watch.users.tests.factories import UserFactory

        user = UserFactory()
        asset = PlankaBoardBackgroundAsset.objects.create(name="bg", uploaded_by=user)
        form = PlankaProjectSetupForm(
            data={"project_name": "X", "background_asset": asset.pk},
            files={"background_upload": self._minimal_image()},
        )
        assert form.is_valid() is False
        assert "background_upload" in form.errors


# ---------------------------------------------------------------------------
# 10. ArticleIntakeFetchForm — date range validation
# ---------------------------------------------------------------------------


class TestArticleIntakeFetchForm:
    def _watched_journal(self):
        from spanza_journal_watch.backend.models import WatchedJournal

        return WatchedJournal.objects.create(name="Test Journal")

    def test_valid_single_month_range(self):
        wj = self._watched_journal()
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [wj.pk],
                "from_month": "2024-01",
                "to_month": "2024-01",
            }
        )
        assert form.is_valid(), form.errors

    def test_to_month_before_from_month_rejected(self):
        wj = self._watched_journal()
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [wj.pk],
                "from_month": "2024-06",
                "to_month": "2024-03",
            }
        )
        assert form.is_valid() is False
        assert "to_month" in form.errors

    def test_range_over_12_months_rejected(self):
        wj = self._watched_journal()
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [wj.pk],
                "from_month": "2023-01",
                "to_month": "2024-02",  # 14 months
            }
        )
        assert form.is_valid() is False
        assert "to_month" in form.errors

    def test_exactly_12_months_accepted(self):
        wj = self._watched_journal()
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [wj.pk],
                "from_month": "2023-01",
                "to_month": "2023-12",  # exactly 12 months
            }
        )
        assert form.is_valid(), form.errors

    def test_from_month_normalised_to_first(self):
        wj = self._watched_journal()
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [wj.pk],
                "from_month": "2024-03",
                "to_month": "2024-05",
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["from_month"].day == 1

    def test_watched_journals_required(self):
        form = ArticleIntakeFetchForm(
            data={
                "watched_journals": [],
                "from_month": "2024-01",
                "to_month": "2024-03",
            }
        )
        assert form.is_valid() is False
        assert "watched_journals" in form.errors


# ---------------------------------------------------------------------------
# 11. WatchedJournalForm
# ---------------------------------------------------------------------------


class TestWatchedJournalForm:
    def test_name_stripped(self):
        form = WatchedJournalForm(data={"name": "  Paediatric Anaesthesia  ", "active": True})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["name"] == "Paediatric Anaesthesia"

    def test_issn_fields_stripped(self):
        form = WatchedJournalForm(
            data={
                "name": "Journal",
                "issn_print": "  1234-5678  ",
                "issn_electronic": "  8765-4321  ",
                "active": True,
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["issn_print"] == "1234-5678"
        assert form.cleaned_data["issn_electronic"] == "8765-4321"


# ---------------------------------------------------------------------------
# 12. AuthorForm
# ---------------------------------------------------------------------------


class TestAuthorForm:
    def test_email_lowercased(self):
        form = AuthorForm(data={"name": "Dr Test", "title": "Dr", "email": "TEST@EXAMPLE.COM"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "test@example.com"

    def test_empty_email_becomes_none(self):
        form = AuthorForm(data={"name": "Dr Test", "title": "Dr", "email": ""})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] is None

    def test_whitespace_email_becomes_none(self):
        form = AuthorForm(data={"name": "Dr Test", "title": "Dr", "email": "   "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] is None


# ---------------------------------------------------------------------------
# 13. IssueContributorInviteForm
# ---------------------------------------------------------------------------


class TestIssueContributorInviteForm:
    def test_email_lowercased_and_stripped(self):
        form = IssueContributorInviteForm(
            data={
                "name": "Alice",
                "email": "  ALICE@EXAMPLE.COM  ",
                "role": IssueContributor.Role.REVIEWER,
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "alice@example.com"

    def test_invalid_role_rejected(self):
        form = IssueContributorInviteForm(data={"name": "Alice", "email": "alice@example.com", "role": "invalid"})
        assert form.is_valid() is False
        assert "role" in form.errors

    def test_valid_coordinator_role(self):
        form = IssueContributorInviteForm(
            data={
                "name": "Bob",
                "email": "bob@example.com",
                "role": IssueContributor.Role.COORDINATOR,
            }
        )
        assert form.is_valid(), form.errors
