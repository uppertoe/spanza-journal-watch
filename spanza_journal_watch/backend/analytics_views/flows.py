"""Visit flows: engagement, event detail enrichment and top paths through the site."""

from collections import Counter, defaultdict

from django.contrib.contenttypes.models import ContentType

from spanza_journal_watch.analytics.models import (
    DELIBERATE_INTERACTION_EVENT_TYPES,
    AnalyticsEvent,
)
from spanza_journal_watch.backend.views.analytics_page import (
    _safe_percentage,
)

from .visits import _VISIT_PAGE_PATHS

_FLOW_LABELS = {
    "review_open": "Impression",
    "review_engaged": "Sustained view",
    "review_full_text_click": "Full text",
    "review_related_click": "Related click",
    "review_share_copy_link": "Share (copy)",
    "review_share_email": "Share (email)",
    "review_share_native": "Share (native)",
    "review_share_bluesky": "Share (Bluesky)",
    "review_share_x": "Share (X)",
    "review_share_facebook": "Share (Facebook)",
    "search": "Search",
    "search_result_click": "Search click",
    "page_visit": "Page visit",
    "journal_browser_visit": "Journals browser",
    "journal_article_interact": "Journal article",
    "journal_select": "Journal selected",
    "journal_star": "Journal starred",
    "journal_recommend": "Journal recommended",
    "journal_mark_read": "Journal marked read",
    "journal_archive": "Journal archived",
    "journal_search": "Journal search",
    "journal_full_text_click": "Journal full text",
    "newsletter_subscribe": "Newsletter subscribe",
    "cpd_tracking_toggle": "CPD tracking toggle",
}


def _visit_is_engaged(visit):
    """Whether a visit represents genuine human engagement.

    Uses the same signal as the headline engaged-humans KPI: a deliberate
    interaction (full-text click, share, search-result click, journal select…)
    or a known subscriber. It deliberately ignores sustained-view dwell and raw
    session duration — the June 2026 bot audit found JS-executing bots game both,
    so counting them here would let the Audience explorer contradict the KPI.
    """
    return any(
        row["event_type"] in DELIBERATE_INTERACTION_EVENT_TYPES
        or row.get("human_confidence") == AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN
        for row in visit["events"]
    )


def _resolve_content_titles(event_ids):
    """Bulk-resolve content_object labels for the given event ids.

    Returns {event_id: "label"}. Labels fall back to the object's __str__.
    """
    if not event_ids:
        return {}
    rows = list(AnalyticsEvent.objects.filter(id__in=event_ids).values("id", "content_type_id", "object_id"))
    # Group event ids by (content_type_id, object_id) to load each object once.
    ids_by_ct = defaultdict(set)
    for row in rows:
        if row["content_type_id"] and row["object_id"]:
            ids_by_ct[row["content_type_id"]].add(row["object_id"])

    titles = {}
    for ct_id, obj_ids in ids_by_ct.items():
        try:
            ct = ContentType.objects.get_for_id(ct_id)
            model = ct.model_class()
        except (ContentType.DoesNotExist, LookupError):
            continue
        if model is None:
            continue
        try:
            for obj_id, obj in model._default_manager.in_bulk(obj_ids).items():
                titles[(ct_id, obj_id)] = str(obj)
        except Exception:
            continue

    result = {}
    for row in rows:
        key = (row["content_type_id"], row["object_id"])
        if key in titles:
            result[row["id"]] = titles[key]
    return result


def _enrich_event_details(event_rows):
    """Fetch duration_ms / scroll_depth / share_token + content titles for event rows."""
    if not event_rows:
        return {}, {}
    ids = [row["id"] for row in event_rows]
    extras = {
        row["id"]: row
        for row in AnalyticsEvent.objects.filter(id__in=ids).values("id", "duration_ms", "scroll_depth", "share_token")
    }
    titles = _resolve_content_titles(ids)
    # Enrich from metadata foreign-key hints that aren't captured via
    # content_object (e.g. journal_select stores journal_id in metadata, not
    # as a GenericForeignKey).
    journal_ids = set()
    article_ids = set()
    for row in event_rows:
        if row["id"] in titles:
            continue
        metadata = row.get("metadata") or {}
        jid = metadata.get("journal_id")
        aid = metadata.get("article_id")
        if isinstance(jid, int) and jid > 0:
            journal_ids.add(jid)
        if isinstance(aid, int) and aid > 0:
            article_ids.add(aid)
    journal_labels = {}
    if journal_ids:
        from spanza_journal_watch.submissions.models import Journal

        journal_labels = {j.pk: str(j) for j in Journal.objects.filter(pk__in=journal_ids)}
    article_labels = {}
    if article_ids:
        from spanza_journal_watch.backend.models import PubmedArticle

        article_labels = {a.pk: a.get_title() for a in PubmedArticle.objects.filter(pk__in=article_ids)}
    for row in event_rows:
        if row["id"] in titles:
            continue
        metadata = row.get("metadata") or {}
        jid = metadata.get("journal_id")
        aid = metadata.get("article_id")
        if isinstance(jid, int) and jid in journal_labels:
            titles[row["id"]] = journal_labels[jid]
        elif isinstance(aid, int) and aid in article_labels:
            titles[row["id"]] = article_labels[aid]
    return extras, titles


def _format_event_detail(event_row, extra, title):
    parts = []
    if title:
        parts.append(title)
    duration_ms = extra.get("duration_ms") if extra else None
    if duration_ms:
        parts.append(f"{duration_ms / 1000:.1f}s")
    scroll = extra.get("scroll_depth") if extra else None
    if scroll is not None and scroll > 0:
        parts.append(f"scroll {scroll}%")
    share_token = (extra or {}).get("share_token")
    if share_token:
        parts.append(f"share {share_token[:8]}")
    metadata = event_row.get("metadata") or {}
    query = metadata.get("query")
    if query:
        parts.append(str(query))
    elif not title:
        path = metadata.get("path") or metadata.get("destination_url")
        if path:
            parts.append(str(path))
        else:
            page = metadata.get("page")
            if page:
                parts.append(_VISIT_PAGE_PATHS.get(page, str(page)))
    return " · ".join(parts)


def _compute_top_flows(visits):
    """Return the top two-step transitions across derived visits.

    Self-transitions (X → X) are dropped — they're usually page_visit / scroll
    flush noise and crowd out genuinely informative movement between surfaces.
    """
    transition_counter = Counter()
    total_visits_with_transition = 0
    for visit in visits:
        event_types = [row["event_type"] for row in visit["events"][:10]]
        seen = set()
        had_transition = False
        for i in range(len(event_types) - 1):
            if event_types[i] == event_types[i + 1]:
                continue
            pair = (event_types[i], event_types[i + 1])
            if pair not in seen:
                transition_counter[pair] += 1
                seen.add(pair)
                had_transition = True
        if had_transition:
            total_visits_with_transition += 1

    return [
        {
            "from": _FLOW_LABELS.get(a, a),
            "to": _FLOW_LABELS.get(b, b),
            "count": count,
            "pct": _safe_percentage(count, total_visits_with_transition),
        }
        for (a, b), count in transition_counter.most_common(12)
    ]
