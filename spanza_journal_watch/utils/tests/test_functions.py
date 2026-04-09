"""
Tests for spanza_journal_watch.utils.functions.

Covers:
1. estimate_reading_time — word count to minutes
2. shorten_text — plain-text truncation at word boundaries
3. HTMLShortener — HTML-safe truncation with tag closing
4. resize_to_max_dimension — aspect-ratio-preserving resize
"""

from spanza_journal_watch.utils.functions import (
    HTMLShortener,
    estimate_reading_time,
    resize_to_max_dimension,
    shorten_text,
)

# ---------------------------------------------------------------------------
# 1. estimate_reading_time
# ---------------------------------------------------------------------------


class TestEstimateReadingTime:
    def test_empty_string_returns_1_minute(self):
        assert estimate_reading_time("") == 1

    def test_short_text_returns_1_minute(self):
        assert estimate_reading_time("Hello world") == 1

    def test_200_words_returns_1_minute(self):
        text = " ".join(["word"] * 200)
        assert estimate_reading_time(text) == 1

    def test_400_words_returns_2_minutes(self):
        text = " ".join(["word"] * 400)
        assert estimate_reading_time(text) == 2

    def test_strips_html_tags(self):
        html = "<p>" + " ".join(["word"] * 400) + "</p>"
        assert estimate_reading_time(html) == 2

    def test_custom_wpm(self):
        text = " ".join(["word"] * 100)
        assert estimate_reading_time(text, words_per_minute=100) == 1

    def test_rounds_to_nearest_minute(self):
        # 350 words at 200 wpm = 1.75 → rounds to 2
        text = " ".join(["word"] * 350)
        assert estimate_reading_time(text) == 2

    def test_rounds_down_when_below_half(self):
        # 210 words at 200 wpm = 1.05 → rounds to 1
        text = " ".join(["word"] * 210)
        assert estimate_reading_time(text) == 1


# ---------------------------------------------------------------------------
# 2. shorten_text
# ---------------------------------------------------------------------------


class TestShortenText:
    def test_short_text_unchanged(self):
        assert shorten_text("Hello", 10) == "Hello"

    def test_exact_limit_unchanged(self):
        assert shorten_text("Hello", 5) == "Hello"

    def test_truncates_at_word_boundary(self):
        assert shorten_text("Hello beautiful world", 16) == "Hello beautiful..."

    def test_no_space_truncates_at_limit(self):
        assert shorten_text("Superlongword", 5) == "Super..."

    def test_empty_string(self):
        assert shorten_text("", 10) == ""


# ---------------------------------------------------------------------------
# 3. HTMLShortener
# ---------------------------------------------------------------------------


class TestHTMLShortener:
    def test_short_html_unchanged(self):
        result = HTMLShortener(100).truncate_html("<p>Short</p>")
        assert result == "<p>Short</p>"

    def test_truncates_text_and_closes_tags(self):
        result = HTMLShortener(5).truncate_html("<p>Hello beautiful world</p>")
        assert result == "<p>Hello...</p>"

    def test_nested_tags_closed(self):
        result = HTMLShortener(5).truncate_html("<div><p><strong>Hello world</strong></p></div>")
        assert result == "<div><p><strong>Hello...</strong></p></div>"

    def test_no_truncation_marker_when_within_limit(self):
        result = HTMLShortener(100).truncate_html("<p>Short</p>")
        assert "..." not in result

    def test_empty_html(self):
        result = HTMLShortener(10).truncate_html("")
        assert result == ""

    def test_plain_text_without_tags(self):
        result = HTMLShortener(5).truncate_html("Hello world")
        assert result == "Hello..."

    def test_multiple_paragraphs(self):
        html = "<p>First paragraph</p><p>Second paragraph</p>"
        result = HTMLShortener(15).truncate_html(html)
        assert result.startswith("<p>First paragra")
        assert result.endswith("</p>")


# ---------------------------------------------------------------------------
# 4. resize_to_max_dimension
# ---------------------------------------------------------------------------


class TestResizeToMaxDimension:
    def test_landscape_image(self):
        w, h = resize_to_max_dimension(1000, 500, 200)
        assert w == 200
        assert h == 100

    def test_portrait_image(self):
        w, h = resize_to_max_dimension(500, 1000, 200)
        assert w == 100
        assert h == 200

    def test_square_image(self):
        w, h = resize_to_max_dimension(800, 800, 400)
        assert w == 400
        assert h == 400

    def test_already_at_target(self):
        w, h = resize_to_max_dimension(200, 100, 200)
        assert w == 200
        assert h == 100
