import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# Matches labels like "BACKGROUND:", "METHODS:", "RESULTS AND DISCUSSION:" at paragraph start
_LABEL_RE = re.compile(r"^([A-Z][A-Z /&-]+):\s*", re.MULTILINE)


@register.filter(needs_autoescape=True)
def format_abstract(value, autoescape=True):
    """Format a PubMed structured abstract with bold section labels and paragraph breaks."""
    if not value:
        return ""
    text = escape(value) if autoescape else value
    paragraphs = text.split("\n\n")
    parts = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para = _LABEL_RE.sub(r'<strong class="text-body-secondary">\1:</strong> ', para)
        parts.append(f"<p>{para}</p>")
    return mark_safe("".join(parts))


@register.filter(needs_autoescape=True)
def strip_abstract_labels(value, autoescape=True):
    """Strip section labels from a structured abstract for use in truncated previews."""
    if not value:
        return ""
    text = escape(value) if autoescape else value
    text = _LABEL_RE.sub("", text)
    return mark_safe(text.replace("\n\n", " "))
