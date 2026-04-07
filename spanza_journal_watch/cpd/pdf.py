from __future__ import annotations

import io
import os
from datetime import date

from fpdf import FPDF

# Path to logo file (relative to this module)
LOGO_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "static",
    "images",
    "logo",
    "spanza-logo-blue.png",
)

# Brand colours
BRAND_NAVY = (30, 68, 104)  # #1e4468 — headings
BRAND_BLUE = (47, 90, 128)  # #2f5a80 — accents
BRAND_MUTED = (138, 150, 162)  # #8a96a2 — secondary text
BODY_TEXT = (51, 51, 51)  # #333333
LIGHT_RULE = (216, 222, 228)  # #d8dee4
DATE_BG = (237, 242, 248)  # #edf2f8 — date subheading background


def _fmt_date(d: date) -> str:
    """Format date as '2 January 2026' (no leading zero)."""
    return f"{d.day} {d.strftime('%B %Y')}"


def _fmt_date_heading(d: date) -> str:
    """Format date as '7th January 2026' for date group headings."""
    day = d.day
    if 11 <= day <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} {d.strftime('%B %Y')}"


def _escape_md(text: str) -> str:
    """Escape fpdf2 markdown markers and replace non-latin-1 chars."""
    text = text.replace("**", "").replace("__", "")
    # Replace smart quotes and other common Unicode with latin-1 equivalents
    text = (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2026", "...")
        .replace("\u00b7", ".")
    )
    # Strip any remaining non-latin-1 characters
    return text.encode("latin-1", errors="replace").decode("latin-1")


def format_vancouver_authors(authors: list[dict], max_authors: int = 6) -> str:
    if not authors:
        return ""
    parts = []
    for a in authors[:max_authors]:
        last = a.get("last_name", "")
        initials = a.get("initials", "")
        parts.append(f"{last} {initials}".strip())
    if len(authors) > max_authors:
        parts.append("et al")
    return ", ".join(parts)


def format_vancouver_citation(article) -> str:
    """Return full Vancouver-style citation with bold title (fpdf2 markdown)."""
    meta = article.metadata_json or {}
    authors = meta.get("authors") or []
    author_str = format_vancouver_authors(authors)

    title = _escape_md((article.title or "").rstrip("."))
    iso_abbrev = meta.get("iso_abbreviation") or article.source_journal_name or ""
    volume = meta.get("volume") or ""
    issue = meta.get("issue") or ""
    pages = meta.get("pages") or ""

    year = ""
    if article.publication_date:
        year = str(article.publication_date.year)

    parts = []
    if author_str:
        parts.append(f"{_escape_md(author_str)}.")
    parts.append(f"**{title}.**")
    if iso_abbrev:
        journal_part = _escape_md(iso_abbrev)
        if year:
            journal_part += f" {year}"
        if volume:
            journal_part += f";{volume}"
        if issue:
            journal_part += f"({issue})"
        if pages:
            journal_part += f":{pages}"
        parts.append(f"{journal_part}.")

    if article.doi:
        parts.append(f"doi: {article.doi}")
    pmid = getattr(article, "pmid", "") or ""
    if pmid:
        parts.append(f"PMID: {pmid}")

    return " ".join(parts)


def generate_cpd_pdf(
    user_name: str,
    user_email: str,
    date_from: date,
    date_to: date,
    articles_by_date: list[tuple[date, list]],
) -> bytes:
    """Generate a CPD activity report PDF.

    ``articles_by_date`` is a list of ``(access_date, [article, ...])`` tuples,
    ordered chronologically.  Each article appears under the date it was first
    accessed.
    """
    total_count = sum(len(arts) for _, arts in articles_by_date)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.set_margin(20)
    pdf.add_page()

    page_w = pdf.epw  # effective page width

    # ── Header band ─────────────────────────────────────────────────
    band_h = 28
    pdf.set_fill_color(*BRAND_NAVY)
    pdf.rect(0, 0, 210, band_h, style="F")

    # Logo in the band
    logo_h = 16
    if os.path.exists(LOGO_PATH):
        pdf.image(LOGO_PATH, x=20, y=6, h=logo_h)

    # Title text in the band
    pdf.set_xy(58, 7)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, "Journal Watch", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(58)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(200, 212, 224)
    pdf.cell(0, 5, "CPD Activity Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(band_h + 10)

    # ── User details ────────────────────────────────────────────────
    details = [
        ("Name", user_name),
        ("Email", user_email),
        ("Period", f"{_fmt_date(date_from)}  -  {_fmt_date(date_to)}"),
        ("Generated", _fmt_date(date.today())),
    ]
    label_w = 22
    for label, value in details:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*BRAND_MUTED)
        pdf.cell(label_w, 5, label, new_x="RIGHT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*BODY_TEXT)
        pdf.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── Explanatory text ────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*BODY_TEXT)
    pdf.multi_cell(
        0,
        5,
        f"This document records the journal articles that {user_name} has "
        f"accessed via SPANZA Journal Watch during the period "
        f"{_fmt_date(date_from)} to {_fmt_date(date_to)}.",
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(*BRAND_MUTED)
    pdf.multi_cell(
        0,
        4.5,
        "The duration of time spent reading these articles should be entered "
        "in the ANZCA CPD portal under Knowledge and Practice: Journal Reading.",
    )
    pdf.ln(6)

    # ── Section header ──────────────────────────────────────────────
    pdf.set_draw_color(*BRAND_BLUE)
    pdf.set_line_width(0.5)
    pdf.line(20, pdf.get_y(), 20 + page_w, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*BRAND_NAVY)
    pdf.cell(0, 6, "Journal articles accessed:", new_x="RIGHT")

    # Article count pill
    count_text = str(total_count)
    pdf.set_font("Helvetica", "B", 9)
    pill_w = pdf.get_string_width(count_text) + 6
    pill_x = 20 + page_w - pill_w
    pill_y = pdf.get_y() + 0.5
    pdf.set_fill_color(*BRAND_BLUE)
    pdf.set_xy(pill_x, pill_y)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(pill_w, 5, count_text, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)

    # ── Date-grouped reference list ─────────────────────────────────
    if not articles_by_date or total_count == 0:
        pdf.set_font("Helvetica", "I", 9.5)
        pdf.set_text_color(*BRAND_MUTED)
        pdf.cell(
            0,
            6,
            "No full-text articles were accessed during this period.",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        ref_num = 0
        num_w = 8

        for access_date, articles in articles_by_date:
            # ── Date subheading ──
            pdf.ln(2)
            y_dh = pdf.get_y()
            pdf.set_fill_color(*DATE_BG)
            pdf.rect(20, y_dh, page_w, 6.5, style="F")
            # Accent bar on the left edge
            pdf.set_fill_color(*BRAND_BLUE)
            pdf.rect(20, y_dh, 1.2, 6.5, style="F")

            pdf.set_xy(24, y_dh + 0.5)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*BRAND_NAVY)
            pdf.cell(0, 5.5, _fmt_date_heading(access_date), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

            # ── Citations for this date ──
            for article in articles:
                ref_num += 1
                citation_md = format_vancouver_citation(article)

                # Number
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(*BRAND_MUTED)
                pdf.cell(num_w, 4.5, f"{ref_num}.", new_x="RIGHT")

                # Full Vancouver citation with bold title
                pdf.set_font("Helvetica", "", 8.5)
                pdf.set_text_color(*BODY_TEXT)
                pdf.multi_cell(page_w - num_w, 4.5, citation_md, markdown=True)

                pdf.ln(1.5)

    # ── Footer rule + text ──────────────────────────────────────────
    pdf.ln(8)
    pdf.set_draw_color(*LIGHT_RULE)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(20, y, 20 + page_w, y)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*BRAND_MUTED)
    pdf.cell(
        0,
        4,
        "Generated by SPANZA Journal Watch  |  journalwatch.org.au",
        align="C",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
