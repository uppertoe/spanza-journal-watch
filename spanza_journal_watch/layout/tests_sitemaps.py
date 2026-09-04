"""
Tests for sitemap classes.

Covers:
1. ReviewSitemap — only active reviews, lastmod present
2. IssueSitemap — only active issues, lastmod present
3. TagSitemap — only active tags (not inactive)
4. AuthorSitemap — excludes anonymous authors
"""

import pytest

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.layout.models import AuthorSitemap, IssueSitemap, ReviewSitemap, TagSitemap
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review, Tag

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. ReviewSitemap
# ---------------------------------------------------------------------------


class TestReviewSitemap:
    def test_inactive_reviews_excluded(self):
        journal = Journal.objects.create(name="Sitemap Journal")
        a1 = PubmedArticle.objects.create(title="Sitemap Active Article", journal=journal, active=True)
        a2 = PubmedArticle.objects.create(title="Sitemap Inactive Article", journal=journal, active=True)
        Review.objects.create(article=a1, body="Body", active=True, slug="sitemap-active-review")
        inactive = Review.objects.create(article=a2, body="Body", active=False, slug="sitemap-inactive-review")

        items = list(ReviewSitemap().items())
        item_pks = [r.pk for r in items]

        assert inactive.pk not in item_pks

    def test_lastmod_returns_modified(self):
        article = PubmedArticle.objects.create(title="Sitemap Lastmod Article")
        review = Review.objects.create(article=article, body="Body", active=True, slug="sitemap-lastmod-review")

        sitemap = ReviewSitemap()
        assert sitemap.lastmod(review) == review.modified


# ---------------------------------------------------------------------------
# 2. IssueSitemap
# ---------------------------------------------------------------------------


class TestIssueSitemap:
    def test_inactive_issues_excluded(self):
        Issue.objects.create(name="Sitemap Active Issue", active=True)
        draft = Issue.objects.create(name="Sitemap Draft Issue", active=False)

        items = list(IssueSitemap().items())
        item_pks = [i.pk for i in items]

        assert draft.pk not in item_pks

    def test_lastmod_returns_modified(self):
        issue = Issue.objects.create(name="Sitemap Lastmod Issue", active=True)

        sitemap = IssueSitemap()
        assert sitemap.lastmod(issue) == issue.modified


# ---------------------------------------------------------------------------
# 3. TagSitemap
# ---------------------------------------------------------------------------


def _live_review(pmid, title):
    article = PubmedArticle.objects.create(pmid=pmid, title=title)
    return article, Review.objects.create(article=article, body="Body " * 40, active=True)


class TestTagSitemap:
    def test_inactive_tags_excluded(self):
        # The sitemap mirrors /explore: curated topics that have at least one live review.
        article, _ = _live_review("880001", "Tagged paper")
        active = Tag.objects.create(text="Sitemap Active Tag Unique", active=True, curated=True)
        inactive = Tag.objects.create(text="Sitemap Inactive Tag Unique", active=False, curated=True)
        active.articles.add(article)
        inactive.articles.add(article)

        items = list(TagSitemap().items())
        item_pks = [t.pk for t in items]

        assert active.pk in item_pks
        assert inactive.pk not in item_pks

    def test_all_items_have_absolute_url(self):
        article, _ = _live_review("880002", "Tagged paper two")
        tag = Tag.objects.create(text="Sitemap URL Tag Unique", active=True, curated=True)
        tag.articles.add(article)

        items = list(TagSitemap().items())

        for item in items:
            assert item.get_absolute_url().startswith("/explore/")


# ---------------------------------------------------------------------------
# 4. AuthorSitemap
# ---------------------------------------------------------------------------


class TestAuthorSitemap:
    def test_anonymous_authors_excluded(self):
        # The sitemap mirrors /about: named contributors with at least one live review.
        named = Author.objects.create(name="Sitemap Real Author", anonymous=False)
        anon = Author.objects.create(name="Sitemap Anonymous", anonymous=True)
        for pmid, author in (("880003", named), ("880004", anon)):
            article = PubmedArticle.objects.create(pmid=pmid, title=f"Paper {pmid}")
            Review.objects.create(article=article, body="Body " * 40, active=True, author=author)

        items = list(AuthorSitemap().items())
        item_pks = [a.pk for a in items]

        assert named.pk in item_pks
        assert anon.pk not in item_pks

    def test_authors_without_live_reviews_excluded(self):
        idle = Author.objects.create(name="Sitemap Idle Author", anonymous=False)
        assert idle.pk not in [a.pk for a in AuthorSitemap().items()]

    def test_all_items_have_absolute_url(self):
        author = Author.objects.create(name="Sitemap URL Author", anonymous=False)
        article = PubmedArticle.objects.create(pmid="880005", title="Paper 880005")
        Review.objects.create(article=article, body="Body " * 40, active=True, author=author)

        items = list(AuthorSitemap().items())

        for item in items:
            assert item.get_absolute_url().startswith("/about/")
