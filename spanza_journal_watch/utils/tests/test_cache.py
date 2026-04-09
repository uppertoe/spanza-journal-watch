"""
Tests for spanza_journal_watch.utils.cache.

Covers:
1. get_content_cache_version — cold start initialises to 1, subsequent calls return current
2. bump_content_cache_version — increments existing, bootstraps when key missing
"""

from django.core.cache import cache
from django.test import TestCase, override_settings

from spanza_journal_watch.utils.cache import (
    CONTENT_CACHE_VERSION_KEY,
    bump_content_cache_version,
    get_content_cache_version,
)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TestGetContentCacheVersion(TestCase):
    def setUp(self):
        cache.clear()

    def test_cold_start_returns_1(self):
        assert get_content_cache_version() == 1

    def test_cold_start_sets_key_in_cache(self):
        get_content_cache_version()
        assert cache.get(CONTENT_CACHE_VERSION_KEY) == 1

    def test_returns_existing_value(self):
        cache.set(CONTENT_CACHE_VERSION_KEY, 42, timeout=None)
        assert get_content_cache_version() == 42


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TestBumpContentCacheVersion(TestCase):
    def setUp(self):
        cache.clear()

    def test_increments_existing_version(self):
        cache.set(CONTENT_CACHE_VERSION_KEY, 5, timeout=None)
        result = bump_content_cache_version()
        assert result == 6
        assert cache.get(CONTENT_CACHE_VERSION_KEY) == 6

    def test_bootstraps_when_key_missing(self):
        result = bump_content_cache_version()
        assert result == 2
        assert cache.get(CONTENT_CACHE_VERSION_KEY) == 2

    def test_successive_bumps_increment(self):
        cache.set(CONTENT_CACHE_VERSION_KEY, 1, timeout=None)
        bump_content_cache_version()
        bump_content_cache_version()
        assert cache.get(CONTENT_CACHE_VERSION_KEY) == 3
