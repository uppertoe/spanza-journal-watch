"""
Tests for the render-path optimisations.

Covers:
1. Markdown HTML memo — identical output, rendered once per body, safe with a dummy cache
2. tags_curated — uses prefetched tags (no extra query) and matches the filtered query
3. Review.search — ranks the stored tsvector directly (no per-row to_tsvector)
"""

from unittest import mock

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from markdownx.utils import markdownify

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions import models as submissions_models
from spanza_journal_watch.submissions.models import Review, Tag, render_review_markdown_html, sanitize_markdown_html

pytestmark = pytest.mark.django_db

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "perf-tests"}}
DUMMY = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}

BODY = "## Heading\n\nSome *emphasis*, a [link](https://example.com) and <script>alert(1)</script>.\n\n- one\n- two\n"


class TestMarkdownMemo:
    @override_settings(CACHES=LOCMEM)
    def test_output_identical_and_rendered_once(self):
        from django.core.cache import cache

        cache.clear()
        expected = sanitize_markdown_html(markdownify(BODY))
        with mock.patch.object(submissions_models, "markdownify", wraps=markdownify) as md:
            first = render_review_markdown_html(BODY)
            second = render_review_markdown_html(BODY)
        assert first == second == expected
        assert md.call_count == 1

    @override_settings(CACHES=LOCMEM)
    def test_review_helpers_unchanged(self):
        from django.core.cache import cache

        cache.clear()
        article = PubmedArticle.objects.create(title="Memo Article", active=True)
        review = Review.objects.create(article=article, body=BODY, active=True, slug="memo-review")
        html = sanitize_markdown_html(markdownify(BODY))
        fresh = Review.objects.get(pk=review.pk)  # instance-level memo not populated
        assert fresh.get_markdown_body() == html
        assert fresh.get_plain_body() == Review.objects.get(pk=review.pk).get_plain_body()
        assert "Heading" not in Review.objects.get(pk=review.pk).get_plain_body(exclude_headings=True)
        assert Review.objects.get(pk=review.pk).get_truncated_body().startswith("Some emphasis, a link")

    @override_settings(CACHES=LOCMEM)
    def test_different_bodies_do_not_collide(self):
        assert render_review_markdown_html("alpha") != render_review_markdown_html("beta")

    @override_settings(CACHES=DUMMY)
    def test_dummy_cache_still_renders(self):
        assert render_review_markdown_html(BODY) == sanitize_markdown_html(markdownify(BODY))


class TestTagsCurated:
    def _article(self):
        article = PubmedArticle.objects.create(title="Tagged Article", active=True)
        curated = Tag.objects.create(text="Curated Tag", curated=True)
        raw = Tag.objects.create(text="Raw Tag", curated=False)
        article.tags.add(curated, raw)
        return article, curated

    def test_prefetched_tags_need_no_extra_query(self):
        article, curated = self._article()
        fetched = PubmedArticle.objects.prefetch_related("tags").get(pk=article.pk)
        with CaptureQueriesContext(connection) as ctx:
            tags = fetched.tags_curated  # property
        assert len(ctx.captured_queries) == 0
        assert [t.pk for t in tags] == [curated.pk]

    def test_without_prefetch_matches(self):
        article, curated = self._article()
        assert list(PubmedArticle.objects.get(pk=article.pk).tags_curated.values_list("pk", flat=True)) == [curated.pk]


class TestSearchRank:
    def test_search_uses_stored_vector_directly(self):
        sql = str(Review.search("airway").query)
        assert '"submissions_review"."search_vector")::text' not in sql
        assert "to_tsvector(COALESCE" not in sql.split("ts_rank(")[1][:80]

    def test_search_finds_body_and_title_matches(self):
        a1 = PubmedArticle.objects.create(title="Dexmedetomidine premedication in children", active=True)
        a2 = PubmedArticle.objects.create(title="Unrelated title", active=True)
        a3 = PubmedArticle.objects.create(title="Another unrelated title", active=True)
        r1 = Review.objects.create(article=a1, body="A review of sedation.", active=True, slug="s-title")
        r2 = Review.objects.create(
            article=a2, body="Dexmedetomidine reduced emergence delirium.", active=True, slug="s-body"
        )
        Review.objects.create(article=a3, body="Nothing relevant here.", active=True, slug="s-none")
        found = {r.pk for r in Review.search("dexmedetomidine")}
        assert found == {r1.pk, r2.pk}
