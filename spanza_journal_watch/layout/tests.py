"""
Tests for the layout app.

Covers:
1. FeatureArticle — slug generation, absolute URL
2. Homepage — publish_homepage, get_current_homepage, get_card_features
3. Cache — publish writes to Redis, get reads from Redis, cache miss falls back to DB,
           deleted object clears cache, non-ready homepage is not cached
4. Favicon file serving — correct status, Cache-Control header, non-empty body
"""

from http import HTTPStatus

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions.models import Issue, Review

from .models import HOMEPAGE_CACHE_KEY, FeatureArticle, Homepage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_homepage(issue=None, publication_ready=True):
    if issue is None:
        issue = Issue.objects.create(name="Test Issue")
    return Homepage.objects.create(issue=issue, publication_ready=publication_ready)


# ---------------------------------------------------------------------------
# 1. FeatureArticle
# ---------------------------------------------------------------------------


class FeatureArticleModelTest(TestCase):
    def test_save_generates_unique_slug(self):
        article1 = FeatureArticle.objects.create(title="Test Article")
        article2 = FeatureArticle.objects.create(title="Test Article")
        self.assertNotEqual(article1.slug, article2.slug)

    def test_get_absolute_url(self):
        article = FeatureArticle.objects.create(title="Test Article")
        url = reverse("layout:feature_article_detail", kwargs={"slug": article.slug})
        self.assertEqual(article.get_absolute_url(), url)


# ---------------------------------------------------------------------------
# 2 & 3. Homepage model + cache behaviour
# ---------------------------------------------------------------------------


class HomepagePublishTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_publish_homepage_sets_cache_key(self):
        hp = _make_homepage()
        Homepage.publish_homepage(hp)
        self.assertEqual(cache.get(HOMEPAGE_CACHE_KEY), hp.pk)

    def test_publish_homepage_does_not_cache_if_not_publication_ready(self):
        hp = _make_homepage(publication_ready=False)
        Homepage.publish_homepage(hp)
        self.assertIsNone(cache.get(HOMEPAGE_CACHE_KEY))

    def test_publish_homepage_replaces_previous_cache_entry(self):
        hp1 = _make_homepage()
        hp2 = _make_homepage(issue=Issue.objects.create(name="Second Issue"))
        Homepage.publish_homepage(hp1)
        Homepage.publish_homepage(hp2)
        self.assertEqual(cache.get(HOMEPAGE_CACHE_KEY), hp2.pk)


class HomepageGetCurrentTest(TestCase):
    def setUp(self):
        cache.clear()
        Homepage.objects.all().delete()

    def test_returns_homepage_from_cache(self):
        hp = _make_homepage()
        cache.set(HOMEPAGE_CACHE_KEY, hp.pk)
        result = Homepage.get_current_homepage()
        self.assertEqual(result, hp)

    def test_cache_miss_falls_back_to_db_and_caches(self):
        hp = _make_homepage()
        # Cache is empty — should hit DB and then populate cache.
        result = Homepage.get_current_homepage()
        self.assertEqual(result, hp)
        self.assertEqual(cache.get(HOMEPAGE_CACHE_KEY), hp.pk)

    def test_cache_miss_with_no_ready_homepage_returns_none(self):
        _make_homepage(publication_ready=False)
        result = Homepage.get_current_homepage()
        self.assertIsNone(result)
        self.assertIsNone(cache.get(HOMEPAGE_CACHE_KEY))

    def test_cache_miss_returns_latest_publication_ready_homepage(self):
        old = _make_homepage(issue=Issue.objects.create(name="Old Issue"))
        new = _make_homepage(issue=Issue.objects.create(name="New Issue"))
        # new was created after old, so it should be returned.
        result = Homepage.get_current_homepage()
        self.assertEqual(result, new)
        _ = old  # suppress unused warning

    def test_stale_cache_pk_clears_cache_and_returns_none(self):
        hp = _make_homepage()
        cache.set(HOMEPAGE_CACHE_KEY, hp.pk)
        hp.delete()
        result = Homepage.get_current_homepage()
        # Object is gone — cache should be cleared and None returned
        # (no other publication_ready homepage exists).
        self.assertIsNone(result)
        self.assertIsNone(cache.get(HOMEPAGE_CACHE_KEY))


# ---------------------------------------------------------------------------
# 4. get_card_features
# ---------------------------------------------------------------------------


class HomepageGetCardFeaturesTest(TestCase):
    def setUp(self):
        cache.clear()
        self.issue = Issue.objects.create(name="Test Issue")
        self.homepage = _make_homepage(issue=self.issue)

    def test_returns_only_featured_active_reviews(self):
        a1 = PubmedArticle.objects.create(title="Article 1")
        a2 = PubmedArticle.objects.create(title="Article 2")
        a3 = PubmedArticle.objects.create(title="Article 3")
        featured = Review.objects.create(article=a1, is_featured=True, active=True)
        Review.objects.create(article=a2, is_featured=False, active=True)  # not featured
        Review.objects.create(article=a3, is_featured=True, active=False)  # not active
        self.issue.reviews.add(featured)
        self.issue.reviews.add(Review.objects.get(article=a2))
        self.issue.reviews.add(Review.objects.get(article=a3))

        card_features = self.homepage.get_card_features()

        self.assertEqual(card_features.count(), 1)
        self.assertEqual(card_features[0], featured)

    def test_returns_empty_queryset_when_no_featured_reviews(self):
        self.assertEqual(self.homepage.get_card_features().count(), 0)


# ---------------------------------------------------------------------------
# 5. Favicon file serving
# ---------------------------------------------------------------------------


class FaviconFileTests(TestCase):
    def test_get(self):
        names = [
            "android-chrome-192x192.png",
            "android-chrome-512x512.png",
            "apple-touch-icon.png",
            "browserconfig.xml",
            "favicon-16x16.png",
            "favicon-32x32.png",
            "favicon.ico",
            "mstile-150x150.png",
            "safari-pinned-tab.svg",
            "site.webmanifest",
        ]

        for name in names:
            with self.subTest(name):
                response = self.client.get(f"/{name}")
                self.assertEqual(response.status_code, HTTPStatus.OK)
                self.assertEqual(
                    response["Cache-Control"],
                    "max-age=86400, immutable, public",
                )
                self.assertGreater(len(response.getvalue()), 0)
