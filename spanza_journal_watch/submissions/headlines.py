"""Editorial headlines and bottom lines for reviews.

Search engines rank a review for the question it answers, not for the title of
the paper it discusses; the paper's own title is always won by the publisher.
This module derives two short pieces of editorial framing from a review:

* ``extract_bottom_line`` pulls the reviewer's own take-home section out of the
  body when one exists (about half of live reviews have one), as plain text.
* ``draft_review_headline`` asks Claude for a headline and a bottom line, using
  the extracted section as the anchor when present. Drafts are suggestions:
  editors see and edit them in the review form before they go live.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape

from django.conf import settings
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

HEADLINE_MAX_LENGTH = 110
BOTTOM_LINE_MAX_WORDS = 70

# Headings that introduce the reviewer's own conclusion, in order of preference.
_TAKEAWAY_HEADINGS = (
    r"take[- ]?home messages?",
    r"take[- ]?home",
    r"bottom line",
    r"key messages?",
    r"key points?",
    r"key takeaways?",
    r"clinical implications?",
    r"in short",
    r"verdict",
    r"conclusions?",
)
_HEADING_LINE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*|\*\*)\s*(?P<heading>[^\n*#]{2,80}?)\s*(?:\*\*)?\s*:?\s*$",
    re.MULTILINE,
)


def _sections(body):
    """Split markdown into (heading, text) pairs; text before the first heading has heading ''."""
    positions = [(m.start(), m.end(), m.group("heading").strip()) for m in _HEADING_LINE.finditer(body)]
    if not positions:
        return [("", body)]
    sections = [("", body[: positions[0][0]])]
    for index, (_start, end, heading) in enumerate(positions):
        next_start = positions[index + 1][0] if index + 1 < len(positions) else len(body)
        sections.append((heading, body[end:next_start]))
    return sections


def _markdown_to_plain(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"[*_`>]+", "", text)
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-+•]|\d+[.)])\s+", "", line)
        if line and line[-1] not in ".!?:;":
            line += "."
        lines.append(line)
    return unescape(strip_tags(" ".join(lines))).strip()


def _first_words(text, limit):
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(",;:") + "…"


def extract_bottom_line(body):
    """Return the reviewer's take-home section as plain text, or '' if there isn't one."""
    if not body:
        return ""
    sections = _sections(body)
    for pattern in _TAKEAWAY_HEADINGS:
        heading_re = re.compile(rf"^{pattern}\b", re.IGNORECASE)
        for heading, text in sections:
            if heading and heading_re.match(heading):
                plain = _markdown_to_plain(text)
                if plain:
                    return _first_words(plain, BOTTOM_LINE_MAX_WORDS)
    return ""


SYSTEM_PROMPT = """You write editorial headlines for SPANZA Journal Watch, a newsletter in which \
paediatric anaesthetists review recent papers for their colleagues. The readers are senior \
clinicians in Australia and New Zealand.

Given a review, write:
1. headline: one line of at most 100 characters that says what the review found or the \
clinical question it settles, in plain Australian English. It must not repeat the paper's \
title, must not be a question unless the review genuinely leaves it open, and must not use \
colons, exclamation marks, quotation marks or promotional language. Prefer a concrete clinical \
claim over a description of the study. Examples of the register: "Neuromuscular block in \
children needs quantitative monitoring, not clinical judgement"; "Strict milk fasting before \
infant anaesthesia adds risk without reducing aspiration".
2. bottom_line: two to four sentences, at most 60 words, giving the reviewer's take-home \
message for practice. If the review contains its own take-home or bottom-line section, \
stay faithful to it and condense it; do not introduce claims the reviewer did not make.

Do not use dashes as sentence separators. Write in the third person about the study; do not \
address the reader."""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "bottom_line": {"type": "string"},
    },
    "required": ["headline", "bottom_line"],
    "additionalProperties": False,
}


def build_client():
    """An Anthropic client, or None when no key is configured."""
    api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    if not api_key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=60.0)


def _review_prompt(review):
    article = review.article
    existing = extract_bottom_line(review.body)
    parts = [
        f"Paper title: {article.get_title().strip()}",
        f"Journal: {article.journal or article.source_journal_name or 'unknown'}",
    ]
    if review.author:
        parts.append(f"Reviewer: {review.author}")
    if existing:
        parts.append(f"The reviewer's own take-home section, verbatim: {existing}")
    parts.append("Review body (markdown):\n\n" + (review.body or "").strip())
    return "\n\n".join(parts)


def draft_review_headline(review, client=None):
    """Return {"headline", "bottom_line"} drafted from the review, or None if unavailable."""
    client = client or build_client()
    if client is None:
        logger.warning("ANTHROPIC_API_KEY is not set; cannot draft a headline for review %s", review.pk)
        return None
    model = getattr(settings, "REVIEW_HEADLINE_MODEL", "claude-opus-5")
    response = client.beta.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _review_prompt(review)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        logger.warning("Headline draft refused for review %s", review.pk)
        return None
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Headline draft for review %s was not valid JSON: %r", review.pk, text[:200])
        return None
    headline = " ".join((data.get("headline") or "").split()).rstrip(".")[:HEADLINE_MAX_LENGTH]
    bottom_line = _first_words(" ".join((data.get("bottom_line") or "").split()), BOTTOM_LINE_MAX_WORDS)
    if not headline:
        return None
    return {"headline": headline, "bottom_line": bottom_line}
