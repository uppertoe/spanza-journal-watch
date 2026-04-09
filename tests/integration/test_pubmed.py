"""
Integration tests for PubMed API client and cache pipeline.

These tests replay recorded cassettes (real PubMed API responses) through the
full client parsing logic, and optionally hit the live API when --live is used.

Run: pytest -m integration tests/integration/test_pubmed.py
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from spanza_journal_watch.backend.pubmed import PubmedClient

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

CASSETTES_DIR = Path(__file__).parent / "cassettes"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_cassette(name):
    path = CASSETTES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"Cassette {name}.json not found")
    with open(path) as f:
        return json.load(f)


def _make_urlopen_replayer(responses):
    """Create a mock urlopen that replays responses in order."""
    call_index = 0

    class FakeResponse:
        def __init__(self, data):
            self._data = data.encode("utf-8") if isinstance(data, str) else data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(request, timeout=None):
        nonlocal call_index
        if call_index >= len(responses):
            raise RuntimeError(f"Unexpected request #{call_index + 1}: {request.full_url}")
        resp = FakeResponse(responses[call_index])
        call_index += 1
        return resp

    return fake_urlopen


# ---------------------------------------------------------------------------
# 1. Article fetch + parse (cassette replay)
# ---------------------------------------------------------------------------


class TestPubmedArticleFetchCassette:
    """Replay recorded esearch+efetch responses through the real parser."""

    @pytest.fixture
    def cassette(self):
        return _load_cassette("pubmed_bja_jan_2026")

    @pytest.fixture
    def client_with_cassette(self, cassette):
        replayer = _make_urlopen_replayer(
            [
                cassette["esearch_response"],
                cassette["efetch_response"],
            ]
        )
        client = PubmedClient(api_key="fake", timeout=5)
        return client, replayer

    def test_search_pmids_returns_expected_ids(self, cassette, client_with_cassette):
        client, replayer = client_with_cassette
        with patch("urllib.request.urlopen", replayer):
            pmids = client.search_pmids(
                '"British Journal of Anaesthesia"[ta]',
                date(2026, 1, 1),
                date(2026, 1, 31),
            )
        assert len(pmids) == len(cassette["pmids"])
        for pmid in cassette["pmids"]:
            assert pmid in pmids

    def test_fetch_articles_parses_all_fields(self, cassette):
        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            articles = client.fetch_articles(cassette["pmids"])

        assert len(articles) == len(cassette["pmids"])
        for article in articles:
            assert article["pmid"]
            assert article["title"]
            assert article["source_journal_name"]
            assert article["publication_date"] is not None
            assert article["metadata_json"]

    def test_parsed_article_has_expected_structure(self, cassette):
        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            articles = client.fetch_articles(cassette["pmids"])

        article = articles[0]
        meta = article["metadata_json"]

        # All expected metadata keys present
        assert "mesh_terms" in meta
        assert "keywords" in meta
        assert "publication_types" in meta
        assert "authors" in meta
        assert "volume" in meta
        assert "issue" in meta
        assert "pages" in meta
        assert "iso_abbreviation" in meta

        # Authors have correct shape
        if meta["authors"]:
            author = meta["authors"][0]
            assert "last_name" in author
            assert "initials" in author

    def test_parsed_dates_are_date_objects(self, cassette):
        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            articles = client.fetch_articles(cassette["pmids"])

        for article in articles:
            assert isinstance(article["publication_date"], date)
            assert isinstance(article["publication_month"], date)
            assert article["publication_month"].day == 1

    def test_bja_articles_have_correct_journal(self, cassette):
        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            articles = client.fetch_articles(cassette["pmids"])

        for article in articles:
            assert "anaesthesia" in article["source_journal_name"].lower()


# ---------------------------------------------------------------------------
# 2. Journal search (cassette replay)
# ---------------------------------------------------------------------------


class TestPubmedJournalSearchCassette:
    @pytest.fixture
    def cassette(self):
        return _load_cassette("pubmed_journal_search_anesthesiology")

    def test_search_journals_returns_results(self, cassette):
        replayer = _make_urlopen_replayer(
            [
                cassette["esearch_ta_response"],
                cassette["esearch_journal_response"],
                cassette["efetch_catalog_response"],
            ]
        )
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            journals = client.search_journals("Anesthesiology", retmax=5)

        assert len(journals) >= 1

    def test_search_journals_parses_fields(self, cassette):
        replayer = _make_urlopen_replayer(
            [
                cassette["esearch_ta_response"],
                cassette["esearch_journal_response"],
                cassette["efetch_catalog_response"],
            ]
        )
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            journals = client.search_journals("Anesthesiology", retmax=5)

        for journal in journals:
            assert journal.get("name")
            assert journal.get("medline_ta")

    def test_exact_match_sorted_first(self, cassette):
        replayer = _make_urlopen_replayer(
            [
                cassette["esearch_ta_response"],
                cassette["esearch_journal_response"],
                cassette["efetch_catalog_response"],
            ]
        )
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            journals = client.search_journals("Anesthesiology", retmax=5)

        if journals:
            first = journals[0]
            # The exact match "Anesthesiology" should be first
            assert first["medline_ta"].lower() == "anesthesiology"


# ---------------------------------------------------------------------------
# 3. Full upsert pipeline (cassette → DB)
# ---------------------------------------------------------------------------


class TestPubmedUpsertPipeline:
    """Test the full fetch→parse→upsert flow using cassette data."""

    @pytest.fixture
    def cassette(self):
        return _load_cassette("pubmed_bja_jan_2026")

    def test_upsert_creates_articles(self, cassette):
        from spanza_journal_watch.backend.models import PubmedArticle
        from spanza_journal_watch.backend.pubmed_cache import upsert_pubmed_article

        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            payloads = client.fetch_articles(cassette["pmids"])

        created_pks = []
        for payload in payloads:
            article = upsert_pubmed_article(payload)
            assert article is not None
            created_pks.append(article.pk)

        assert PubmedArticle.objects.filter(pk__in=created_pks).count() == len(payloads)

    def test_upsert_is_idempotent(self, cassette):
        from spanza_journal_watch.backend.pubmed_cache import upsert_pubmed_article

        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            payloads = client.fetch_articles(cassette["pmids"])

        # First pass
        first_pks = []
        for payload in payloads:
            article = upsert_pubmed_article(payload)
            first_pks.append(article.pk)

        # Second pass — same data, should return same articles
        second_pks = []
        for payload in payloads:
            article = upsert_pubmed_article(payload)
            second_pks.append(article.pk)

        assert first_pks == second_pks

    def test_upsert_fills_all_fields(self, cassette):
        from spanza_journal_watch.backend.pubmed_cache import upsert_pubmed_article

        replayer = _make_urlopen_replayer([cassette["efetch_response"]])
        client = PubmedClient(api_key="fake", timeout=5)
        with patch("urllib.request.urlopen", replayer):
            payloads = client.fetch_articles(cassette["pmids"])

        article = upsert_pubmed_article(payloads[0])
        assert article.pmid
        assert article.title
        assert article.source_journal_name
        assert article.publication_date is not None
        assert article.metadata_json.get("authors")


# ---------------------------------------------------------------------------
# 4. PubmedClient unit-level integration (parsing edge cases from real data)
# ---------------------------------------------------------------------------


class TestPubmedClientHelpers:
    def test_month_to_bounds(self):
        start, end = PubmedClient.month_to_bounds(date(2026, 1, 1), date(2026, 3, 1))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 3, 31)

    def test_month_to_bounds_february(self):
        start, end = PubmedClient.month_to_bounds(date(2026, 2, 1), date(2026, 2, 1))
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 28)

    def test_parse_month_names(self):
        assert PubmedClient._parse_month("Jan") == 1
        assert PubmedClient._parse_month("dec") == 12
        assert PubmedClient._parse_month("6") == 6
        assert PubmedClient._parse_month("") is None
        assert PubmedClient._parse_month("13") is None
