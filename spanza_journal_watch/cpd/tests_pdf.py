"""
Tests for CPD PDF utility functions.

Covers:
1. _fmt_date — date formatting without leading zero
2. _fmt_date_heading — ordinal suffix date formatting
3. _escape_md — markdown/Unicode escaping
4. format_vancouver_authors — author list formatting
5. format_vancouver_citation — full citation formatting
"""

from datetime import date
from types import SimpleNamespace

from spanza_journal_watch.cpd.pdf import (
    _escape_md,
    _fmt_date,
    _fmt_date_heading,
    format_vancouver_authors,
    format_vancouver_citation,
)

# ---------------------------------------------------------------------------
# 1. _fmt_date
# ---------------------------------------------------------------------------


class TestFmtDate:
    def test_basic(self):
        assert _fmt_date(date(2026, 1, 2)) == "2 January 2026"

    def test_no_leading_zero(self):
        result = _fmt_date(date(2026, 3, 5))
        assert result.startswith("5 ")

    def test_double_digit_day(self):
        assert _fmt_date(date(2026, 12, 25)) == "25 December 2026"


# ---------------------------------------------------------------------------
# 2. _fmt_date_heading
# ---------------------------------------------------------------------------


class TestFmtDateHeading:
    def test_first(self):
        assert _fmt_date_heading(date(2026, 1, 1)) == "1st January 2026"

    def test_second(self):
        assert _fmt_date_heading(date(2026, 1, 2)) == "2nd January 2026"

    def test_third(self):
        assert _fmt_date_heading(date(2026, 1, 3)) == "3rd January 2026"

    def test_fourth(self):
        assert _fmt_date_heading(date(2026, 1, 4)) == "4th January 2026"

    def test_eleventh(self):
        assert _fmt_date_heading(date(2026, 1, 11)) == "11th January 2026"

    def test_twelfth(self):
        assert _fmt_date_heading(date(2026, 1, 12)) == "12th January 2026"

    def test_thirteenth(self):
        assert _fmt_date_heading(date(2026, 1, 13)) == "13th January 2026"

    def test_twenty_first(self):
        assert _fmt_date_heading(date(2026, 1, 21)) == "21st January 2026"

    def test_twenty_second(self):
        assert _fmt_date_heading(date(2026, 1, 22)) == "22nd January 2026"

    def test_thirty_first(self):
        assert _fmt_date_heading(date(2026, 1, 31)) == "31st January 2026"


# ---------------------------------------------------------------------------
# 3. _escape_md
# ---------------------------------------------------------------------------


class TestEscapeMd:
    def test_strips_bold_markers(self):
        assert "**" not in _escape_md("**bold text**")

    def test_strips_underline_markers(self):
        assert "__" not in _escape_md("__underline__")

    def test_replaces_smart_quotes(self):
        result = _escape_md("\u2018hello\u2019")
        assert result == "'hello'"

    def test_replaces_em_dash(self):
        assert "-" in _escape_md("word\u2014word")

    def test_replaces_ellipsis(self):
        assert "..." in _escape_md("wait\u2026")

    def test_strips_non_latin1(self):
        # Characters outside latin-1 get replaced
        result = _escape_md("hello \u4e16\u754c")
        assert "hello" in result


# ---------------------------------------------------------------------------
# 4. format_vancouver_authors
# ---------------------------------------------------------------------------


class TestFormatVancouverAuthors:
    def test_empty_list(self):
        assert format_vancouver_authors([]) == ""

    def test_single_author(self):
        authors = [{"last_name": "Smith", "initials": "JA"}]
        assert format_vancouver_authors(authors) == "Smith JA"

    def test_multiple_authors(self):
        authors = [
            {"last_name": "Smith", "initials": "JA"},
            {"last_name": "Jones", "initials": "B"},
        ]
        assert format_vancouver_authors(authors) == "Smith JA, Jones B"

    def test_truncates_at_max(self):
        authors = [{"last_name": f"Author{i}", "initials": "X"} for i in range(8)]
        result = format_vancouver_authors(authors, max_authors=6)
        assert "et al" in result
        assert "Author6" not in result

    def test_no_et_al_at_max(self):
        authors = [{"last_name": f"Author{i}", "initials": "X"} for i in range(6)]
        result = format_vancouver_authors(authors, max_authors=6)
        assert "et al" not in result

    def test_missing_initials(self):
        authors = [{"last_name": "Solo"}]
        assert format_vancouver_authors(authors) == "Solo"


# ---------------------------------------------------------------------------
# 5. format_vancouver_citation
# ---------------------------------------------------------------------------


class TestFormatVancouverCitation:
    def test_basic_citation(self):
        article = SimpleNamespace(
            metadata_json={
                "authors": [{"last_name": "Smith", "initials": "J"}],
            },
            title="Test Article Title",
            source_journal_name="Test Journal",
            publication_date=date(2026, 3, 15),
            doi="10.1234/test",
            pmid="12345678",
        )
        result = format_vancouver_citation(article)
        assert "Smith J" in result
        assert "Test Article Title" in result
        assert "Test Journal" in result
        assert "2026" in result

    def test_no_authors(self):
        article = SimpleNamespace(
            metadata_json={},
            title="Orphan Article",
            source_journal_name="J",
            publication_date=date(2026, 1, 1),
            doi="",
            pmid="",
        )
        result = format_vancouver_citation(article)
        assert "Orphan Article" in result

    def test_includes_doi(self):
        article = SimpleNamespace(
            metadata_json={"authors": []},
            title="DOI Article",
            source_journal_name="J",
            publication_date=date(2026, 1, 1),
            doi="10.1234/doi-test",
            pmid="",
        )
        result = format_vancouver_citation(article)
        assert "10.1234/doi-test" in result

    def test_includes_pmid(self):
        article = SimpleNamespace(
            metadata_json={"authors": []},
            title="PMID Article",
            source_journal_name="J",
            publication_date=date(2026, 1, 1),
            doi="",
            pmid="99887766",
        )
        result = format_vancouver_citation(article)
        assert "99887766" in result
