import pytest

from spanza_journal_watch.backend.models import PubmedArticle

pytestmark = pytest.mark.django_db


class TestILikeContains:
    def test_matches_case_insensitively(self):
        PubmedArticle.objects.create(pmid="90000001", title="Caudal Block in Infants", abstract="")
        assert PubmedArticle.objects.filter(title__ilike_contains="caudal block").count() == 1
        assert PubmedArticle.objects.filter(title__ilike_contains="spinal").count() == 0

    def test_wildcards_in_the_query_are_literal(self):
        PubmedArticle.objects.create(pmid="90000002", title="100% oxygen", abstract="")
        PubmedArticle.objects.create(pmid="90000003", title="100 percent oxygen", abstract="")
        assert PubmedArticle.objects.filter(title__ilike_contains="100%").count() == 1
        assert PubmedArticle.objects.filter(title__ilike_contains="_").count() == 0

    def test_renders_as_ilike(self):
        sql = str(PubmedArticle.objects.filter(abstract__ilike_contains="child").query)
        assert "ILIKE" in sql
        assert "UPPER(" not in sql
