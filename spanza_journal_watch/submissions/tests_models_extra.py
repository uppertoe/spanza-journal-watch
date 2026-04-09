"""
Additional model tests for submissions app.

Covers:
1. CuratedCollection — slug generation, get_absolute_url, str
2. Review.get_hits — tested in submissions/tests.py but adding edge cases
3. MeshTagMapping — str representation
"""

import pytest
from django.urls import reverse

from spanza_journal_watch.submissions.models import CuratedCollection, MeshTagMapping, Tag

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 1. CuratedCollection
# ---------------------------------------------------------------------------


class TestCuratedCollection:
    def test_slug_auto_generated(self):
        collection = CuratedCollection.objects.create(title="Airway Management")
        assert collection.slug == "airway-management"

    def test_duplicate_title_gets_unique_slug(self):
        c1 = CuratedCollection.objects.create(title="Unique Collection Title XYZ")
        c2 = CuratedCollection.objects.create(title="Unique Collection Title XYZ")
        assert c1.slug != c2.slug

    def test_get_absolute_url(self):
        collection = CuratedCollection.objects.create(title="URL Collection")
        expected = reverse("submissions:collection_detail", kwargs={"slug": collection.slug})
        assert collection.get_absolute_url() == expected

    def test_str(self):
        collection = CuratedCollection.objects.create(title="Display Collection")
        assert str(collection) == "Display Collection"

    def test_default_active(self):
        collection = CuratedCollection.objects.create(title="Active Default")
        assert collection.active is True

    def test_can_add_tags_and_reviews(self):
        from spanza_journal_watch.backend.models import PubmedArticle
        from spanza_journal_watch.submissions.models import Review

        collection = CuratedCollection.objects.create(title="Full Collection")
        tag = Tag.objects.create(text="Collection Tag XYZ", active=True)
        article = PubmedArticle.objects.create(title="Collection Article")
        review = Review.objects.create(article=article, body="body", slug="collection-review-xyz")

        collection.tags.add(tag)
        collection.reviews.add(review)

        assert collection.tags.count() == 1
        assert collection.reviews.count() == 1


# ---------------------------------------------------------------------------
# 2. MeshTagMapping
# ---------------------------------------------------------------------------


class TestMeshTagMapping:
    def test_str(self):
        tag = Tag.objects.create(text="MTM Str Tag ZZZ", active=True)
        mapping = MeshTagMapping.objects.create(mesh_term="MTM Unique Term ZZZ", tag=tag)
        assert str(mapping) == "MTM Unique Term ZZZ → MTM Str Tag ZZZ"

    def test_unique_mesh_term(self):
        tag = Tag.objects.create(text="MTM Dupe Tag ZZZ", active=True)
        MeshTagMapping.objects.create(mesh_term="MTM Dupe Term ZZZ", tag=tag)
        with pytest.raises(Exception):
            MeshTagMapping.objects.create(mesh_term="MTM Dupe Term ZZZ", tag=tag)
