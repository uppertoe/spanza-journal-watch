import os
from pathlib import Path

from django.conf import settings

from spanza_journal_watch.utils.regression_snapshots import normalize_html  # noqa: F401


def snapshot_file(name: str) -> Path:
    return Path(settings.BASE_DIR) / "tests" / "regression" / "snapshots" / f"{name}.html"


# Editorial pages compared strictly against tests/regression/snapshots/<name>.html,
# rendered as the fixture's first superuser. Regenerate after an intended change with:
#   JW_UPDATE_SNAPSHOTS=1 pytest tests/regression -k editorial_page_matches_snapshot
EDITORIAL_SNAPSHOT_ROUTES = {
    "editorial_dashboard": "/editorial/",
    "editorial_issue_builder": "/editorial/issues/builder",
    "editorial_issue_reviewers": "/editorial/issues/reviewers",
    "editorial_issue_reviews": "/editorial/issues/reviews",
    "editorial_issue_publish": "/editorial/issues/publish",
    "editorial_issue_planka": "/editorial/issues/planka",
    "editorial_article_intake": "/editorial/articles/intake",
    "editorial_watched_journals": "/editorial/articles/watched-journals",
    "editorial_subscribers": "/editorial/subscribers/list",
    "editorial_newsletter_release": "/editorial/newsletter/release",
    "editorial_analytics_overview": "/editorial/analytics/overview/",
    "editorial_analytics_editorial": "/editorial/analytics/editorial/",
    "editorial_analytics_traffic": "/editorial/analytics/traffic/",
    # Pinned window. The analytics panels count fixture rows inside a range
    # ending today, and the fixture is a static JSON, so a rolling window walks
    # off its own data: this snapshot was generated on 2026-09-08 with three
    # subscribers in the last 180 days, and silently became two when the
    # 2026-03-22 row aged out around 2026-09-18. Naming the window that was in
    # effect at generation makes the counts deterministic. The dates themselves
    # are masked by normalize_html, so pinning them costs the snapshot nothing.
    "editorial_analytics_email": "/editorial/analytics/email/?start=2026-03-12&end=2026-09-08",
    "editorial_analytics_journals": "/editorial/analytics/journals/",
    "editorial_analytics_issues": "/editorial/analytics/issues/",
    "editorial_inbox": "/editorial/inbox/",
    "editorial_settings": "/editorial/settings",
    "editorial_tags": "/editorial/settings/tags",
    "editorial_collections": "/editorial/settings/collections",
    "editorial_authors": "/editorial/authors",
    "editorial_affiliations": "/editorial/affiliations",
    "user_update": "/users/~update/",
}


def assert_matches_snapshot(name: str, html: str) -> None:
    """Compare normalised HTML with the stored snapshot; write it instead when JW_UPDATE_SNAPSHOTS=1.

    On a mismatch the rendered version is left beside the snapshot as ``<name>.actual.html``
    so the two can be diffed.
    """
    actual = normalize_html(html)
    expected_path = snapshot_file(name)
    if os.environ.get("JW_UPDATE_SNAPSHOTS") == "1":
        expected_path.write_text(actual, encoding="utf-8")
        return
    assert expected_path.exists(), f"Missing snapshot {expected_path.name}; run with JW_UPDATE_SNAPSHOTS=1"
    expected = normalize_html(expected_path.read_text(encoding="utf-8"))
    if actual != expected:
        expected_path.with_suffix(".actual.html").write_text(actual, encoding="utf-8")
    assert actual == expected, f"{name} differs from its snapshot{_first_difference(expected, actual)}"


def _first_difference(expected: str, actual: str, window: int = 90) -> str:
    """A readable excerpt around the first differing character.

    Normalised snapshots are a single long line, so a unified diff is unreadable
    and the .actual.html the caller is pointed at does not survive CI. Showing
    the two sides around the first divergence is what actually identifies the
    culprit - usually an unmasked date that has rolled over.
    """
    limit = min(len(expected), len(actual))
    at = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    start = max(0, at - window)
    return (
        f" at character {at}:\n"
        f"  expected: …{expected[start : at + window]}…\n"
        f"  actual:   …{actual[start : at + window]}…"
    )
