"""
Tests for submissions/signals.py cache invalidation.

Covers:
1. post_save on content models bumps cache version
2. post_delete on content models bumps cache version
3. M2M changes (Issue.reviews, Tag.articles) bump cache version
4. raw=True (fixture loading) does NOT bump cache version
"""

from unittest.mock import patch

import pytest

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions.models import Author, Issue, Review, Tag

pytestmark = pytest.mark.django_db


@pytest.fixture()
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


BUMP_PATH = "spanza_journal_watch.submissions.signals.bump_content_cache_version"


class TestCacheInvalidationOnSave:
    def test_tag_save_bumps_cache(self, _clear_cache):
        with patch(BUMP_PATH) as mock_bump:
            Tag.objects.create(text="Signal Test Tag", active=True)
        mock_bump.assert_called()

    def test_author_save_bumps_cache(self, _clear_cache):
        with patch(BUMP_PATH) as mock_bump:
            Author.objects.create(name="Signal Test Author")
        mock_bump.assert_called()

    def test_issue_save_bumps_cache(self, _clear_cache):
        with patch(BUMP_PATH) as mock_bump:
            Issue.objects.create(name="Signal Test Issue", body="body")
        mock_bump.assert_called()

    def test_review_save_bumps_cache(self, _clear_cache):
        article = PubmedArticle.objects.create(title="Signal Test Article")
        with patch(BUMP_PATH) as mock_bump:
            Review.objects.create(article=article, body="body", slug="signal-test-review")
        mock_bump.assert_called()

    def test_pubmed_article_save_bumps_cache(self, _clear_cache):
        with patch(BUMP_PATH) as mock_bump:
            PubmedArticle.objects.create(title="Signal PubMed Article")
        mock_bump.assert_called()


class TestCacheInvalidationOnDelete:
    def test_tag_delete_bumps_cache(self, _clear_cache):
        tag = Tag.objects.create(text="Delete Signal Tag", active=True)
        with patch(BUMP_PATH) as mock_bump:
            tag.delete()
        mock_bump.assert_called()

    def test_author_delete_bumps_cache(self, _clear_cache):
        author = Author.objects.create(name="Delete Signal Author")
        with patch(BUMP_PATH) as mock_bump:
            author.delete()
        mock_bump.assert_called()


class TestCacheInvalidationOnM2MChange:
    def test_issue_reviews_add_bumps_cache(self, _clear_cache):
        article = PubmedArticle.objects.create(title="M2M Article")
        review = Review.objects.create(article=article, body="body", slug="m2m-review")
        issue = Issue.objects.create(name="M2M Issue", body="body")
        with patch(BUMP_PATH) as mock_bump:
            issue.reviews.add(review)
        mock_bump.assert_called()

    def test_issue_reviews_remove_bumps_cache(self, _clear_cache):
        article = PubmedArticle.objects.create(title="M2M Remove Article")
        review = Review.objects.create(article=article, body="body", slug="m2m-remove-review")
        issue = Issue.objects.create(name="M2M Remove Issue", body="body")
        issue.reviews.add(review)
        with patch(BUMP_PATH) as mock_bump:
            issue.reviews.remove(review)
        mock_bump.assert_called()

    def test_tag_articles_add_bumps_cache(self, _clear_cache):
        tag = Tag.objects.create(text="M2M Tag", active=True)
        article = PubmedArticle.objects.create(title="M2M Tag Article")
        with patch(BUMP_PATH) as mock_bump:
            tag.articles.add(article)
        mock_bump.assert_called()

    def test_issue_reviews_clear_bumps_cache(self, _clear_cache):
        article = PubmedArticle.objects.create(title="M2M Clear Article")
        review = Review.objects.create(article=article, body="body", slug="m2m-clear-review")
        issue = Issue.objects.create(name="M2M Clear Issue", body="body")
        issue.reviews.add(review)
        with patch(BUMP_PATH) as mock_bump:
            issue.reviews.clear()
        mock_bump.assert_called()


class TestRawSaveSkipsBump:
    def test_raw_save_does_not_bump(self, _clear_cache):
        """When loading fixtures (raw=True), signals should not bump cache."""
        from django.db.models.signals import post_save

        with patch(BUMP_PATH) as mock_bump:
            # Simulate a raw save by sending the signal directly
            tag = Tag(text="Raw Tag", active=True)
            tag.save()
            mock_bump.reset_mock()

            # Now send signal with raw=True
            post_save.send(sender=Tag, instance=tag, raw=True, created=False)

        # The raw=True call should not have triggered a bump
        mock_bump.assert_not_called()
