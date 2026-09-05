"""HTML normalisation shared by the regression snapshot command and the regression tests.

A snapshot compares the ``<main>`` element of a page after the per-request
noise has been replaced with stable placeholders: CSRF tokens, bundle
filenames, timestamps and relative times.
"""

import re

_RELATIVE_TIME = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|[smhd])\s+ago\b",
    re.IGNORECASE,
)


def normalize_html(html: str) -> str:
    normalized = html
    normalized = re.sub(r"csrfmiddlewaretoken[^\"]+\"", 'csrfmiddlewaretoken" value="__CSRF__"', normalized)
    normalized = re.sub(
        r"name=['\"]csrfmiddlewaretoken['\"] value=['\"][^'\"]+['\"]",
        'name="csrfmiddlewaretoken" value="__CSRF__"',
        normalized,
    )
    normalized = re.sub(r"X-CSRFToken\"\] = \"[^\"]+\"", 'X-CSRFToken"] = "__CSRF__"', normalized)
    normalized = re.sub(r'id="rev-csrf-token" value="[^"]+"', 'id="rev-csrf-token" value="__CSRF__"', normalized)
    normalized = re.sub(
        r"<!-- Vendor dependencies bundled as one file -->.*?(?=</head>)",
        "<!-- Vendor dependencies bundled as one file --> __BUNDLES__ ",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    normalized = re.sub(
        r"<div class=\"[^\"]*bd-mode-toggle[^\"]*\"[^>]*>.*?</ul>\s*</div>",
        '<div class="dropdown position-fixed bottom-0 end-0 bd-mode-toggle">__THEME_TOGGLE__</div>',
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    normalized = re.sub(
        r"<script[^>]+(?:https?://localhost(?::\d+)?|)/static/bundles/[^>]*></script>",
        '<script src="/static/bundles/__BUNDLE__.js" defer></script>',
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"https?://localhost(?::\d+)?/static/bundles/[^\"']+",
        "http://localhost/static/bundles/__BUNDLE__.js",
        normalized,
    )
    normalized = re.sub(r"/static/bundles/[^\"']+", "/static/bundles/__BUNDLE__.js", normalized)
    normalized = re.sub(
        r"/static/webpack_bundles/(css|js|fonts)/([A-Za-z_]+)[-.][0-9a-f]{8,}",
        r"/static/webpack_bundles/\1/\2.__HASH__",
        normalized,
    )
    normalized = re.sub(
        r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:\d{2}|Z)?",
        "__DATETIME__",
        normalized,
    )
    normalized = _RELATIVE_TIME.sub("__AGO__", normalized)
    # Calendar dates: analytics ranges and date inputs default to today, so they
    # would otherwise change every day.
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "__DATE__", normalized)
    normalized = re.sub(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b", "__DATE__", normalized)
    main_match = re.search(r"<main[^>]*>.*?</main>", normalized, flags=re.IGNORECASE | re.DOTALL)
    if main_match:
        normalized = main_match.group(0)
    normalized = re.sub(r">\s+<", "><", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized + "\n"
