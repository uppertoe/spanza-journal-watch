"""
Tests for IndexNow submission.

Covers:
1. Key file — served at /<key>.txt only when it matches settings, 404 otherwise
2. submit_urls — disabled without a key; payload shape, absolute URLs, dedup
3. sitemap_paths — includes home and active reviews
4. Publish hook — publishing an issue queues the changed URLs
"""

from unittest import mock

import pytest
from django.test import override_settings

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions.models import Review
from spanza_journal_watch.utils import indexnow

pytestmark = pytest.mark.django_db

KEY = "0123456789abcdef0123456789abcdef"


class TestKeyFile:
    @override_settings(INDEXNOW_KEY=KEY)
    def test_key_served_as_plain_text(self, client):
        response = client.get(f"/{KEY}.txt")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert response.content.decode() == KEY

    @override_settings(INDEXNOW_KEY=KEY)
    def test_other_keys_404(self, client):
        assert client.get("/ffffffffffffffff.txt").status_code == 404

    @override_settings(INDEXNOW_KEY="")
    def test_404_when_disabled(self, client):
        assert client.get(f"/{KEY}.txt").status_code == 404


class TestSubmitUrls:
    @override_settings(INDEXNOW_KEY="", DEBUG=False)
    def test_disabled_without_key(self):
        with mock.patch.object(indexnow.requests, "post") as post:
            assert indexnow.submit_urls(["/"]) == 0
        post.assert_not_called()

    @override_settings(INDEXNOW_KEY=KEY, DEBUG=True)
    def test_disabled_in_debug(self):
        with mock.patch.object(indexnow.requests, "post") as post:
            assert indexnow.submit_urls(["/"]) == 0
        post.assert_not_called()

    @override_settings(INDEXNOW_KEY=KEY, DEBUG=False, INDEXNOW_ENDPOINT="https://api.indexnow.org/indexnow")
    def test_posts_expected_payload(self):
        with mock.patch.object(indexnow.requests, "post") as post:
            post.return_value = mock.Mock(status_code=200, text="")
            submitted = indexnow.submit_urls(["/", "/reviews/foo", "/reviews/foo", "https://example.com/issues/bar"])

        assert submitted == 3
        post.assert_called_once()
        endpoint = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        assert endpoint == "https://api.indexnow.org/indexnow"
        assert payload["host"] == "example.com"
        assert payload["key"] == KEY
        assert payload["keyLocation"] == f"https://example.com/{KEY}.txt"
        assert payload["urlList"] == [
            "https://example.com/",
            "https://example.com/reviews/foo",
            "https://example.com/issues/bar",
        ]

    @override_settings(INDEXNOW_KEY=KEY, DEBUG=False)
    def test_rejected_batch_counts_as_zero(self):
        with mock.patch.object(indexnow.requests, "post") as post:
            post.return_value = mock.Mock(status_code=422, text="Invalid key")
            assert indexnow.submit_urls(["/"]) == 0


class TestSitemapPaths:
    def test_includes_home_and_active_reviews(self):
        article = PubmedArticle.objects.create(title="IndexNow Article", active=True)
        review = Review.objects.create(article=article, body="Body", active=True, slug="indexnow-review")
        inactive_article = PubmedArticle.objects.create(title="IndexNow Draft", active=True)
        draft = Review.objects.create(article=inactive_article, body="Body", active=False, slug="indexnow-draft")

        paths = indexnow.sitemap_paths()

        assert paths[0] == "/"
        assert review.get_absolute_url() in paths
        assert draft.get_absolute_url() not in paths
        assert len(paths) == len(set(paths))


class TestQueueSubmission:
    @override_settings(INDEXNOW_KEY=KEY, DEBUG=False)
    def test_delay_called_when_enabled(self):
        from spanza_journal_watch.submissions import tasks

        with mock.patch.object(tasks.submit_urls_to_indexnow, "delay") as delay:
            tasks.queue_indexnow_submission(["/", "/reviews/foo"])
        delay.assert_called_once_with(["/", "/reviews/foo"])

    @override_settings(INDEXNOW_KEY="", DEBUG=False)
    def test_noop_when_disabled(self):
        from spanza_journal_watch.submissions import tasks

        with mock.patch.object(tasks.submit_urls_to_indexnow, "delay") as delay:
            tasks.queue_indexnow_submission(["/"])
        delay.assert_not_called()

    @override_settings(INDEXNOW_KEY=KEY, DEBUG=False)
    def test_broker_error_is_swallowed(self):
        from spanza_journal_watch.submissions import tasks

        with mock.patch.object(tasks.submit_urls_to_indexnow, "delay", side_effect=OSError("broker down")):
            tasks.queue_indexnow_submission(["/"])
