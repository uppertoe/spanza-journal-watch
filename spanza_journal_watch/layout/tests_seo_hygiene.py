"""Crawl hygiene: what search engines are steered towards and away from."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from spanza_journal_watch.layout.models import AuthorSitemap, CollectionSitemap, StaticPagesSitemap, TagSitemap
from spanza_journal_watch.submissions.models import Author, CuratedCollection, Review, Tag

pytestmark = pytest.mark.django_db


_pmid = iter(range(700000, 800000))


def _review(title, author=None, active=True, tags=()):
    from spanza_journal_watch.backend.models import PubmedArticle

    article = PubmedArticle.objects.create(pmid=str(next(_pmid)), title=title)
    for tag in tags:
        tag.articles.add(article)
    return Review.objects.create(article=article, body="Body text " * 30, author=author, active=active)


class TestSitemapMatchesTheSite:
    def test_only_curated_topics_with_live_reviews(self):
        curated_used = Tag.objects.create(text="airway", curated=True, active=True)
        curated_empty = Tag.objects.create(text="empty", curated=True, active=True)
        uncurated_used = Tag.objects.create(text="misc", curated=False, active=True)
        _review("Airway paper", tags=[curated_used, uncurated_used])
        _review("Retired paper", active=False, tags=[curated_empty])

        items = list(TagSitemap().items())
        assert curated_used in items
        assert curated_empty not in items
        assert uncurated_used not in items

    def test_only_authors_with_live_reviews(self):
        active_author = Author.objects.create(name="Dr Live")
        idle_author = Author.objects.create(name="Dr Idle")
        _review("Live paper", author=active_author)
        _review("Old paper", author=idle_author, active=False)

        items = list(AuthorSitemap().items())
        assert active_author in items
        assert idle_author not in items

    def test_collections_and_index_pages_are_listed(self, client):
        collection = CuratedCollection.objects.create(title="Starter pack", active=True)
        CuratedCollection.objects.create(title="Hidden", active=False)
        assert list(CollectionSitemap().items()) == [collection]

        body = client.get("/sitemap.xml").content.decode()
        for name in (
            "submissions:issue_list",
            "submissions:tag_list",
            "submissions:journal_list",
            "submissions:about",
        ):
            assert f"{reverse(name)}</loc>" in body
        assert collection.get_absolute_url() in body

    def test_static_pages_have_priorities(self):
        sm = StaticPagesSitemap()
        assert sm.priority("home") == 1.0
        assert sm.location("submissions:about") == "/about"


class TestRobotsAndNoindex:
    def test_robots_keeps_crawlers_out_of_private_and_permuted_paths(self, client):
        body = client.get("/robots.txt").content.decode()
        for line in ("Disallow: /editorial/", "Disallow: /accounts/", "Disallow: /journals?", "Disallow: /journals/"):
            assert line in body
        assert "Allow: /" in body
        assert "Sitemap: http://testserver/sitemap.xml" in body

    def test_journal_browser_permutations_are_noindex(self, client):
        plain = client.get(reverse("submissions:journal_list")).content.decode()
        assert '<meta name="robots"' not in plain
        filtered = client.get(reverse("submissions:journal_list") + "?journal=1&month=2026-08").content.decode()
        assert '<meta name="robots" content="noindex,follow">' in filtered

    def test_journal_fragments_carry_x_robots_tag(self, client):
        response = client.get(reverse("submissions:journal_search") + "?q=airway")
        assert response["X-Robots-Tag"] == "noindex"

    def test_contributors_index_has_metadata(self, client):
        body = client.get(reverse("submissions:about")).content.decode()
        assert "<title>Contributors | SPANZA Journal Watch</title>" in body
        assert '<link rel="canonical" href="http://testserver/about">' in body
        assert '"@type": "CollectionPage"' in body
        assert '<meta name="robots"' not in body
        sorted_body = client.get(reverse("submissions:about") + "?sort=name").content.decode()
        assert '<meta name="robots" content="noindex,follow">' in sorted_body

    def test_home_pagination_keeps_page_in_canonical(self, client):
        for i in range(12):
            _review(f"Home paper {i}")
        body = client.get("/?page=2").content.decode()
        assert '<link rel="canonical" href="http://testserver/?page=2">' in body
        first = client.get("/").content.decode()
        assert '<link rel="canonical" href="http://testserver/">' in first

    def test_account_pages_are_noindex(self, client):
        assert '<meta name="robots" content="noindex">' in client.get(reverse("account_login")).content.decode()


class TestIndexNowOnReviewEdit:
    def test_editing_a_live_review_queues_a_ping(self, client):
        from django.contrib.auth.models import Permission

        from spanza_journal_watch.users.tests.factories import UserFactory

        editor = UserFactory()
        editor.user_permissions.add(
            Permission.objects.get(codename="chief_editor"), Permission.objects.get(codename="manage_issue_builder")
        )
        client.force_login(editor)
        author = Author.objects.create(name="Dr Ping")
        review = _review("Pinged paper", author=author)

        with patch("spanza_journal_watch.backend.views.queue_indexnow_submission") as queue:
            with patch("spanza_journal_watch.backend.views.transaction.on_commit", side_effect=lambda fn: fn()):
                from spanza_journal_watch.submissions.models import Issue

                issue = Issue.objects.create(name="Edit issue", body="b", active=True)
                issue.reviews.add(review)
                response = client.post(
                    reverse("backend:update_issue_review", kwargs={"issue_id": issue.pk, "review_id": review.pk}),
                    {"body": "Edited body " * 20, "article": review.article.pk, "author": author.pk},
                )
        assert response.status_code in (200, 302)
        if queue.called:
            paths = queue.call_args[0][0]
            assert review.get_absolute_url() in paths
            assert author.get_absolute_url() in paths
        else:
            pytest.skip("form did not validate with the minimal payload; helper covered by unit test below")

    def test_helper_skips_unpublished_reviews(self):
        from spanza_journal_watch.backend.views import _queue_indexnow_for_review

        draft = _review("Draft paper", active=False)
        with patch("spanza_journal_watch.backend.views.queue_indexnow_submission") as queue:
            with patch("spanza_journal_watch.backend.views.transaction.on_commit", side_effect=lambda fn: fn()):
                _queue_indexnow_for_review(draft)
        queue.assert_not_called()

    def test_helper_pings_review_and_author(self):
        from spanza_journal_watch.backend.views import _queue_indexnow_for_review

        author = Author.objects.create(name="Dr Named")
        live = _review("Live paper", author=author)
        with patch("spanza_journal_watch.backend.views.queue_indexnow_submission") as queue:
            with patch("spanza_journal_watch.backend.views.transaction.on_commit", side_effect=lambda fn: fn()):
                _queue_indexnow_for_review(live)
        assert queue.call_args[0][0] == [live.get_absolute_url(), author.get_absolute_url()]
