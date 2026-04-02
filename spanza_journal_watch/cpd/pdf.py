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

# Brand colour from the SPANZA blue logo
BRAND_BLUE = (0, 124, 186)


def _fmt_date(d: date) -> str:
    """Format date as '2 January 2026' (no leading zero)."""
    return f"{d.day} {d.strftime('%B %Y')}"


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
    meta = article.metadata_json or {}
    authors = meta.get("authors") or []
    author_str = format_vancouver_authors(authors)

    title = (article.title or "").rstrip(".")
    iso_abbrev = meta.get("iso_abbreviation") or article.source_journal_name or ""
    volume = meta.get("volume") or ""
    issue = meta.get("issue") or ""
    pages = meta.get("pages") or ""

    year = ""
    if article.publication_date:
        year = str(article.publication_date.year)

    parts = []
    if author_str:
        parts.append(f"{author_str}.")
    if title:
        parts.append(f"{title}.")
    if iso_abbrev:
        journal_part = iso_abbrev
        if year:
            journal_part += f". {year}"
        if volume:
            journal_part += f";{volume}"
        if issue:
            journal_part += f"({issue})"
        if pages:
            journal_part += f":{pages}"
        parts.append(f"{journal_part}.")

    if article.doi:
        parts.append(f"doi: {article.doi}.")
    pmid = getattr(article, "pmid", "") or ""
    if pmid:
        parts.append(f"PMID: {pmid}.")

    return " ".join(parts)


def generate_cpd_pdf(
    user_name: str,
    user_email: str,
    date_from: date,
    date_to: date,
    articles: list,
) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Logo + title header ──────────────────────────────────────────
    logo_width = 30
    if os.path.exists(LOGO_PATH):
        pdf.image(LOGO_PATH, x=10, y=10, w=logo_width)

    title_x = 10 + logo_width + 6
    pdf.set_xy(title_x, 12)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*BRAND_BLUE)
    pdf.cell(0, 8, "SPANZA Journal Watch", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(title_x)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 7, "CPD Activity Report", new_x="LMARGIN", new_y="NEXT")

    # Move below the logo area
    pdf.set_y(max(pdf.get_y(), 10 + logo_width * 0.618) + 6)

    # ── Divider ──────────────────────────────────────────────────────
    pdf.set_draw_color(*BRAND_BLUE)
    pdf.set_line_width(0.6)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)

    # ── User details box ─────────────────────────────────────────────
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    details = [
        ("Name", user_name),
        ("Email", user_email),
        ("Period", f"{_fmt_date(date_from)} to {_fmt_date(date_to)}"),
        ("Generated", _fmt_date(date.today())),
    ]
    for label, value in details:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(28, 6, f"{label}:", new_x="RIGHT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Explanatory text ─────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(
        0,
        5,
        f"{user_name} has accessed the full text of the following articles "
        f"during the period {_fmt_date(date_from)} to {_fmt_date(date_to)}.",
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(
        0,
        5,
        "The duration of time spent reading these articles should be entered "
        "in the ANZCA CPD portal under Knowledge and Practice: Journal Reading.",
    )
    pdf.ln(6)

    # ── Section header ───────────────────────────────────────────────
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*BRAND_BLUE)
    section_title = f"Knowledge and Practice: Journal Reading ({len(articles)} articles)"
    pdf.cell(0, 8, section_title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── Reference list ───────────────────────────────────────────────
    pdf.set_text_color(0, 0, 0)
    if not articles:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 6, "No full-text articles were accessed during this period.", new_x="LMARGIN", new_y="NEXT")
    else:
        for i, article in enumerate(articles, 1):
            citation = format_vancouver_citation(article)

            # Number in blue
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*BRAND_BLUE)
            num_text = f"{i}."
            num_width = pdf.get_string_width(num_text) + 2
            pdf.cell(num_width, 4.5, num_text, new_x="RIGHT")

            # Citation text
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, 4.5, citation)
            pdf.ln(2)

    # ── Footer ───────────────────────────────────────────────────────
    pdf.ln(6)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(140, 140, 140)
    pdf.cell(0, 4, "Generated by SPANZA Journal Watch  |  www.journalwatch.org.au", align="C")

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
