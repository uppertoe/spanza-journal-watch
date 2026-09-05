"""Public site views for reviews, issues, topics, search, contributors and the journal browser.

Grouped by page in the modules below and re-exported here so ``urls.py`` and other
apps keep importing from ``spanza_journal_watch.submissions.views``; new code should
import from the specific module and add new views to the import block for their
module below (and to ``__all__``)."""

from .authors import (
    AuthorDetailView,
    HealthServiceListView,
)
from .issues import (
    IssueDetailView,
    IssueListView,
    LatestIssueView,
)
from .journal_browser import (
    _PAEDIATRIC_MESH_TERMS,
    _PAEDIATRIC_TEXT_TERMS,
    IGNORED_PUBLICATION_TYPES,
    JOURNAL_SECTIONS,
    JournalListView,
    _attach_related_reviews,
    _attach_related_reviews_to_issue_page,
    _best_default_month,
    _group_articles_by_section,
    _journal_article_actions_context,
    _journal_browser_context,
    _journal_month_options,
    _parse_journal_month,
    _toggle_visitor_recommendation,
    journal_article_mark_fulltext,
    journal_article_toggle_archive,
    journal_article_toggle_recommend,
    journal_article_toggle_star,
    journal_fulltext_ids,
    journal_reading_list,
    journal_search,
    journal_shelf_hide,
    journal_shelf_show_all,
)
from .reviews import (
    ReviewDetailView,
)
from .search import (
    SearchView,
)
from .shared import (
    META_DESCRIPTION_MIN_LENGTH,
    SEO_TITLE_MAX_LENGTH,
    attach_review_display_fields,
    build_absolute_url,
    build_paginated_canonical_url,
    build_request_absolute_url,
    build_review_meta_description,
    build_share_urls,
)
from .tags import (
    CuratedCollectionDetailView,
    TagDetailView,
    TagListView,
    _curated_tag_queryset_with_review_count,
    ajax_get_tags,
)

__all__ = [
    "AuthorDetailView",
    "CuratedCollectionDetailView",
    "HealthServiceListView",
    "IGNORED_PUBLICATION_TYPES",
    "IssueDetailView",
    "IssueListView",
    "JOURNAL_SECTIONS",
    "JournalListView",
    "LatestIssueView",
    "META_DESCRIPTION_MIN_LENGTH",
    "ReviewDetailView",
    "SEO_TITLE_MAX_LENGTH",
    "SearchView",
    "TagDetailView",
    "TagListView",
    "_PAEDIATRIC_MESH_TERMS",
    "_PAEDIATRIC_TEXT_TERMS",
    "_attach_related_reviews",
    "_attach_related_reviews_to_issue_page",
    "_best_default_month",
    "_curated_tag_queryset_with_review_count",
    "_group_articles_by_section",
    "_journal_article_actions_context",
    "_journal_browser_context",
    "_journal_month_options",
    "_parse_journal_month",
    "_toggle_visitor_recommendation",
    "ajax_get_tags",
    "attach_review_display_fields",
    "build_absolute_url",
    "build_paginated_canonical_url",
    "build_request_absolute_url",
    "build_review_meta_description",
    "build_share_urls",
    "journal_article_mark_fulltext",
    "journal_article_toggle_archive",
    "journal_article_toggle_recommend",
    "journal_article_toggle_star",
    "journal_fulltext_ids",
    "journal_reading_list",
    "journal_search",
    "journal_shelf_hide",
    "journal_shelf_show_all",
]
