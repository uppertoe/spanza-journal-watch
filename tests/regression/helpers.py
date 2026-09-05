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
    "editorial_analytics_email": "/editorial/analytics/email/",
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
    assert actual == expected, f"{name} differs from its snapshot (see {expected_path.stem}.actual.html)"
