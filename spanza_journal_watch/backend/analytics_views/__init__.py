"""Analytics views for the editorial backend.

Five tabs, each answering a specific question:
  Overview - How is the site performing?
  Editorial Intelligence - What should we cover next?
  Audience - Who reads and how do they find us?
  Newsletter Impact - Is the newsletter driving engagement?
  Feature Adoption - Are our features being used?

One module per panel plus shared helpers; everything is re-exported here so
``urls.py`` and tests keep importing from ``spanza_journal_watch.backend.analytics_views``."""

from .benchmarks import (
    _BENCHMARK_LOOKBACK_DAYS,
    _BENCHMARK_MIN_COHORT,
    _FIRST_WINDOW_DAYS,
    _REFERRER_CATEGORY_LABELS,
    _benchmark_verdict,
    _confidence_summary,
    _first_window_benchmark,
    _first_window_opens,
    _is_one_step_visit,
    _referrer_source_breakdown,
    _review_publish_date,
)
from .common import (
    VIEW_NEWSLETTER_STATS,
    VIEW_SITE_ANALYTICS,
    _base_event_qs,
    _date_range_from_request,
    _engaged_human_count,
    _newsletter_send_weeks,
    _pct_change,
    _render_analytics,
    _weekly_buckets,
)
from .content import (
    _CONTENT_SEARCH_LIMIT,
    analytics_content,
    analytics_content_search,
)
from .editorial import (
    analytics_editorial,
    analytics_search,
)
from .email import (
    _acquisition_summary,
    _recent_subscriber_feed,
    analytics_email,
)
from .flows import (
    _FLOW_LABELS,
    _compute_top_flows,
    _enrich_event_details,
    _format_event_detail,
    _resolve_content_titles,
    _visit_is_engaged,
)
from .issues import (
    _ISSUE_SHARE_EVENT_TYPES,
    _issue_page_visits_by_slug,
    analytics_issue_detail,
    analytics_issues,
)
from .journals import (
    analytics_journals,
)
from .overview import (
    analytics_overview,
)
from .reviews import (
    analytics_review_timeline,
)
from .traffic import (
    analytics_traffic,
)
from .visitors import (
    analytics_visit_events,
    analytics_visitor,
)
from .visits import (
    _CACHE_MISS,
    _DERIVED_VISITS_CACHE_PREFIX,
    _JOURNAL_EVENT_TYPES,
    _LANDING_PAGE_EXACT_EXCLUSIONS,
    _LANDING_PAGE_PREFIX_EXCLUSIONS,
    _LANDING_PAGE_SUFFIX_EXCLUSIONS,
    _LOW_SAMPLE_THRESHOLD,
    _PAGE_SECTION_LABELS,
    _PLACEHOLDER_SEARCH_QUERIES,
    _VISIT_INACTIVITY_GAP,
    _VISIT_PAGE_PATHS,
    _VISIT_PROGRESSION_EVENT_TYPES,
    _build_derived_visits,
    _build_derived_visits_cached,
    _derive_page_section,
    _derive_visit_landing_page,
    _derived_visits_ttl,
    _is_reportable_landing_page,
    _normalise_search_query,
    _rank_rows,
    _split_new_returning,
    _utm_field_from_metadata,
    _visit_partition_key,
    _weekly_visit_buckets,
    _weekly_visits_by_referrer,
)

__all__ = [
    "VIEW_NEWSLETTER_STATS",
    "VIEW_SITE_ANALYTICS",
    "_BENCHMARK_LOOKBACK_DAYS",
    "_BENCHMARK_MIN_COHORT",
    "_CACHE_MISS",
    "_CONTENT_SEARCH_LIMIT",
    "_DERIVED_VISITS_CACHE_PREFIX",
    "_FIRST_WINDOW_DAYS",
    "_FLOW_LABELS",
    "_ISSUE_SHARE_EVENT_TYPES",
    "_JOURNAL_EVENT_TYPES",
    "_LANDING_PAGE_EXACT_EXCLUSIONS",
    "_LANDING_PAGE_PREFIX_EXCLUSIONS",
    "_LANDING_PAGE_SUFFIX_EXCLUSIONS",
    "_LOW_SAMPLE_THRESHOLD",
    "_PAGE_SECTION_LABELS",
    "_PLACEHOLDER_SEARCH_QUERIES",
    "_REFERRER_CATEGORY_LABELS",
    "_VISIT_INACTIVITY_GAP",
    "_VISIT_PAGE_PATHS",
    "_VISIT_PROGRESSION_EVENT_TYPES",
    "_acquisition_summary",
    "_base_event_qs",
    "_benchmark_verdict",
    "_build_derived_visits",
    "_build_derived_visits_cached",
    "_compute_top_flows",
    "_confidence_summary",
    "_date_range_from_request",
    "_derive_page_section",
    "_derive_visit_landing_page",
    "_derived_visits_ttl",
    "_engaged_human_count",
    "_enrich_event_details",
    "_first_window_benchmark",
    "_first_window_opens",
    "_format_event_detail",
    "_is_one_step_visit",
    "_is_reportable_landing_page",
    "_issue_page_visits_by_slug",
    "_newsletter_send_weeks",
    "_normalise_search_query",
    "_pct_change",
    "_rank_rows",
    "_recent_subscriber_feed",
    "_referrer_source_breakdown",
    "_render_analytics",
    "_resolve_content_titles",
    "_review_publish_date",
    "_split_new_returning",
    "_utm_field_from_metadata",
    "_visit_is_engaged",
    "_visit_partition_key",
    "_weekly_buckets",
    "_weekly_visit_buckets",
    "_weekly_visits_by_referrer",
    "analytics_content",
    "analytics_content_search",
    "analytics_editorial",
    "analytics_email",
    "analytics_issue_detail",
    "analytics_issues",
    "analytics_journals",
    "analytics_overview",
    "analytics_review_timeline",
    "analytics_search",
    "analytics_traffic",
    "analytics_visit_events",
    "analytics_visitor",
]
