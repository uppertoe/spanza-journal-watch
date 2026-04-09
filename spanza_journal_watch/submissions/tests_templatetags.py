"""
Tests for submissions template tags.

Covers:
1. format_abstract — structured abstract formatting with bold labels and paragraph breaks
2. strip_abstract_labels — label stripping for truncated previews
3. wrapchars — long-word breaking
"""

from django.utils.safestring import SafeString

from spanza_journal_watch.submissions.templatetags.format_abstract import (
    format_abstract,
    strip_abstract_labels,
)
from spanza_journal_watch.submissions.templatetags.wrapchars import wrapchars

# ---------------------------------------------------------------------------
# 1. format_abstract
# ---------------------------------------------------------------------------


class TestFormatAbstract:
    def test_empty_value_returns_empty_string(self):
        assert format_abstract("") == ""
        assert format_abstract(None) == ""

    def test_plain_text_wrapped_in_paragraph(self):
        result = format_abstract("Some plain text", autoescape=False)
        assert result == "<p>Some plain text</p>"

    def test_section_label_bolded(self):
        result = format_abstract("BACKGROUND: Some background info", autoescape=False)
        assert '<strong class="text-body-secondary">BACKGROUND:</strong>' in result

    def test_multiple_paragraphs_split(self):
        text = "First paragraph\n\nSecond paragraph"
        result = format_abstract(text, autoescape=False)
        assert "<p>First paragraph</p>" in result
        assert "<p>Second paragraph</p>" in result

    def test_structured_abstract_with_labels(self):
        text = "BACKGROUND: Context here\n\nMETHODS: Method details\n\nRESULTS: Outcome data"
        result = format_abstract(text, autoescape=False)
        assert result.count("<strong") == 3
        assert result.count("<p>") == 3

    def test_autoescape_escapes_html(self):
        result = format_abstract("<script>alert('xss')</script>", autoescape=True)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_returns_safe_string(self):
        result = format_abstract("Some text", autoescape=True)
        assert isinstance(result, SafeString)

    def test_blank_paragraphs_skipped(self):
        text = "First\n\n\n\nSecond"
        result = format_abstract(text, autoescape=False)
        assert result.count("<p>") == 2

    def test_label_with_slash(self):
        result = format_abstract("RESULTS AND DISCUSSION: Findings here", autoescape=False)
        assert "RESULTS AND DISCUSSION:" in result


# ---------------------------------------------------------------------------
# 2. strip_abstract_labels
# ---------------------------------------------------------------------------


class TestStripAbstractLabels:
    def test_empty_value_returns_empty_string(self):
        assert strip_abstract_labels("") == ""
        assert strip_abstract_labels(None) == ""

    def test_labels_removed(self):
        text = "BACKGROUND: Some context\n\nMETHODS: Some method"
        result = strip_abstract_labels(text, autoescape=False)
        assert "BACKGROUND:" not in result
        assert "METHODS:" not in result
        assert "Some context" in result
        assert "Some method" in result

    def test_paragraph_breaks_collapsed_to_spaces(self):
        text = "First paragraph\n\nSecond paragraph"
        result = strip_abstract_labels(text, autoescape=False)
        assert "\n\n" not in result
        assert "First paragraph Second paragraph" in result

    def test_returns_safe_string(self):
        result = strip_abstract_labels("Some text", autoescape=True)
        assert isinstance(result, SafeString)


# ---------------------------------------------------------------------------
# 3. wrapchars
# ---------------------------------------------------------------------------


class TestWrapChars:
    def test_short_word_unchanged(self):
        assert wrapchars("Hello") == "Hello"

    def test_long_word_gets_break(self):
        long_word = "a" * 60
        result = wrapchars(long_word)
        assert "<br>" in result

    def test_normal_sentence_unchanged(self):
        text = "This is a normal sentence"
        assert wrapchars(text) == text

    def test_break_inserted_at_45_chars(self):
        long_word = "a" * 90
        result = wrapchars(long_word)
        # Should insert <br> after every 45 characters
        assert result.count("<br>") == 2

    def test_mixed_short_and_long_words(self):
        text = "short " + "a" * 60
        result = wrapchars(text)
        assert result.startswith("short ")
        assert "<br>" in result
