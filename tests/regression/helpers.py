import re
from pathlib import Path

from django.conf import settings


def normalize_html(html: str) -> str:
    normalized = html
    normalized = re.sub(r"csrfmiddlewaretoken[^\"]+\"", 'csrfmiddlewaretoken" value="__CSRF__"', normalized)
    normalized = re.sub(
        r"name=['\"]csrfmiddlewaretoken['\"] value=['\"][^'\"]+['\"]",
        'name="csrfmiddlewaretoken" value="__CSRF__"',
        normalized,
    )
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
        r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2})?",
        "__DATETIME__",
        normalized,
    )
    main_match = re.search(r"<main[^>]*>.*?</main>", normalized, flags=re.IGNORECASE | re.DOTALL)
    if main_match:
        normalized = main_match.group(0)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized + "\n"


def snapshot_file(name: str) -> Path:
    return Path(settings.BASE_DIR) / "tests" / "regression" / "snapshots" / f"{name}.html"
