import datetime
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    PubmedArticle,
    PubmedBatchArticle,
    PubmedIntegrationCredential,
    WatchedJournal,
    WatchedJournalArticle,
)
from .pubmed import PubmedClient, fetch_crossref_journal_articles, fetch_crossref_metadata

logger = logging.getLogger(__name__)


def build_pubmed_client(api_key=""):
    key = (api_key or "").strip()
    if not key:
        credential = PubmedIntegrationCredential.get_solo()
        key = credential.get_api_key() if credential else ""
    return PubmedClient(
        api_key=key,
        timeout=int(getattr(settings, "PUBMED_TIMEOUT_SECONDS", 20)),
        tool=str(getattr(settings, "PUBMED_TOOL_NAME", "spanza-journal-watch")),
        email=str(
            getattr(
                settings,
                "PUBMED_CONTACT_EMAIL",
                getattr(settings, "DEFAULT_FROM_EMAIL", "queries@journalwatch.org.au"),
            )
        ),
    )


def build_pubmed_term(watched_journal):
    # Prefer MedlineTA with [ta] tag — most reliable PubMed journal identifier
    if watched_journal.medline_ta:
        return f'"{watched_journal.medline_ta.strip()}"[ta]'

    issn_terms = []
    if watched_journal.issn_print:
        issn_terms.append(f'"{watched_journal.issn_print.strip()}"[ISSN]')
    if watched_journal.issn_electronic:
        issn_terms.append(f'"{watched_journal.issn_electronic.strip()}"[ISSN]')
    if issn_terms:
        return "(" + " OR ".join(issn_terms) + ")"
    return f'"{watched_journal.name.strip()}"[Journal]'


def build_accepted_journal_names(watched_journal):
    """Build a set of accepted journal name variants for post-fetch validation."""
    names = set()
    for field in ("name", "display_name", "medline_ta", "iso_abbreviation"):
        value = (getattr(watched_journal, field, "") or "").strip()
        if value:
            names.add(value.lower())
    return names


def article_matches_journal(payload, accepted_names):
    """Check whether a fetched article's journal matches the expected watched journal."""
    if not accepted_names:
        return True  # No names to validate against — accept everything

    source_journal = (payload.get("source_journal_name") or "").strip().lower()
    iso_abbrev = ((payload.get("metadata_json") or {}).get("iso_abbreviation") or "").strip().lower()

    for name in (source_journal, iso_abbrev):
        if name and name in accepted_names:
            return True
    return False


def shift_month(date_value, delta_months):
    month_index = (date_value.year * 12 + (date_value.month - 1)) + int(delta_months)
    year = month_index // 12
    month = (month_index % 12) + 1
    return datetime.date(year, month, 1)


def default_pubmed_cache_window(anchor_date=None):
    anchor = (anchor_date or timezone.now().date()).replace(day=1)
    return shift_month(anchor, -2), shift_month(anchor, 2)


def article_metadata_list(article, key):
    data = article.metadata_json or {}
    values = data.get(key) or []
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def article_matches_metadata(article, key, accepted_values):
    accepted_lower = {item.lower() for item in accepted_values}
    values_lower = {item.lower() for item in article_metadata_list(article, key)}
    return bool(values_lower.intersection(accepted_lower))


def article_matches_text(article, accepted_terms):
    text = " ".join(
        [
            (article.title or ""),
            (article.abstract or ""),
            " ".join(article_metadata_list(article, "keywords")),
            " ".join(article_metadata_list(article, "mesh_terms")),
        ]
    ).lower()
    return any(term.lower() in text for term in accepted_terms)


def article_matches_topic(article, *, mesh_terms=None, text_terms=None):
    mesh_terms = mesh_terms or set()
    text_terms = text_terms or set()
    return article_matches_metadata(article, "mesh_terms", mesh_terms) or article_matches_text(article, text_terms)


def fill_missing_article_metadata(article, payload):
    changed = False
    fields = (
        "title",
        "abstract",
        "source_journal_name",
        "publication_date",
        "publication_month",
        "article_url",
        "pubmed_url",
    )

    for field in fields:
        incoming = payload.get(field)
        current = getattr(article, field)
        if field in {"publication_date", "publication_month"}:
            if incoming and incoming != current:
                setattr(article, field, incoming)
                changed = True
            continue

        if (not current) and incoming:
            setattr(article, field, incoming)
            changed = True

    if not article.doi and payload.get("doi"):
        article.doi = payload.get("doi")
        changed = True

    incoming_metadata = payload.get("metadata_json") or {}
    existing_metadata = article.metadata_json or {}
    if incoming_metadata:
        for key in ("mesh_terms", "keywords", "publication_types", "authors"):
            existing_values = existing_metadata.get(key) or []
            incoming_values = incoming_metadata.get(key) or []
            if not existing_values and incoming_values:
                existing_metadata[key] = incoming_values
                changed = True
        for key in ("volume", "issue", "pages", "iso_abbreviation"):
            if not existing_metadata.get(key) and incoming_metadata.get(key):
                existing_metadata[key] = incoming_metadata[key]
                changed = True
        if changed:
            article.metadata_json = existing_metadata

    if changed:
        article.save()


@transaction.atomic
def upsert_pubmed_article(payload):
    pmid = (payload.get("pmid") or "").strip() or None
    doi = (payload.get("doi") or "").strip().lower() or None
    if not pmid and not doi:
        return None

    article = None
    if doi:
        article = PubmedArticle.objects.filter(doi=doi).first()
    if not article and pmid:
        article = PubmedArticle.objects.filter(pmid=pmid).first()

    if not article:
        return PubmedArticle.objects.create(
            pmid=pmid,
            doi=doi,
            title=payload.get("title") or "",
            abstract=payload.get("abstract") or "",
            source_journal_name=payload.get("source_journal_name") or "",
            publication_date=payload.get("publication_date"),
            publication_month=payload.get("publication_month"),
            article_url=payload.get("article_url") or "",
            pubmed_url=payload.get("pubmed_url") or "",
            metadata_json=payload.get("metadata_json") or {},
        )

    fill_missing_article_metadata(article, payload)
    return article


def refresh_watched_journal_cache(watched_journal, from_month, to_month, *, client=None, seen_pmids=None):
    client = client or build_pubmed_client()
    seen_pmids = seen_pmids if seen_pmids is not None else set()
    accepted_names = build_accepted_journal_names(watched_journal)
    history = client.search_pmids_history(build_pubmed_term(watched_journal), from_month, to_month)
    now = timezone.now()
    created_links = 0
    touched_links = 0
    rejected = 0

    for payload in client.fetch_articles_history(history["webenv"], history["query_key"], history["count"]):
        pmid = (payload.get("pmid") or "").strip()
        if not pmid or pmid in seen_pmids:
            continue
        seen_pmids.add(pmid)

        if not article_matches_journal(payload, accepted_names):
            rejected += 1
            continue

        article = upsert_pubmed_article(payload)
        if article is None:
            continue

        link, created = WatchedJournalArticle.objects.get_or_create(
            watched_journal=watched_journal,
            article=article,
            defaults={
                "publication_month": payload.get("publication_month") or article.publication_month,
                "first_seen_at": now,
                "last_seen_at": now,
            },
        )
        touched_links += 1
        if created:
            created_links += 1
            continue

        update_fields = ["last_seen_at", "modified"]
        link.last_seen_at = now
        publication_month = payload.get("publication_month") or article.publication_month
        if publication_month and link.publication_month != publication_month:
            link.publication_month = publication_month
            update_fields.append("publication_month")
        link.save(update_fields=update_fields)

    if rejected:
        logger.info(
            "Watched journal %s: rejected %d article(s) that didn't match accepted names",
            watched_journal,
            rejected,
        )
    return {"created_links": created_links, "touched_links": touched_links, "rejected": rejected}


def refresh_crossref_journal_cache(watched_journal, from_month, to_month, *, seen_dois=None):
    """Refresh cached articles for a CrossRef-sourced journal."""
    seen_dois = seen_dois if seen_dois is not None else set()
    accepted_names = build_accepted_journal_names(watched_journal)
    issn = watched_journal.issn_electronic or watched_journal.issn_print
    if not issn:
        logger.warning("CrossRef journal %s has no ISSN — skipping", watched_journal.name)
        return {"created_links": 0, "touched_links": 0, "rejected": 0}

    now = timezone.now()
    created_links = 0
    touched_links = 0
    rejected = 0

    for payload in fetch_crossref_journal_articles(issn, from_month, to_month):
        doi = (payload.get("doi") or "").strip().lower()
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)

        if not article_matches_journal(payload, accepted_names):
            rejected += 1
            continue

        article = upsert_pubmed_article(payload)
        if article is None:
            continue

        link, created = WatchedJournalArticle.objects.get_or_create(
            watched_journal=watched_journal,
            article=article,
            defaults={
                "publication_month": payload.get("publication_month") or article.publication_month,
                "first_seen_at": now,
                "last_seen_at": now,
            },
        )
        touched_links += 1
        if created:
            created_links += 1
            continue

        update_fields = ["last_seen_at", "modified"]
        link.last_seen_at = now
        publication_month = payload.get("publication_month") or article.publication_month
        if publication_month and link.publication_month != publication_month:
            link.publication_month = publication_month
            update_fields.append("publication_month")
        link.save(update_fields=update_fields)

    if rejected:
        logger.info(
            "CrossRef journal %s: rejected %d article(s) that didn't match accepted names",
            watched_journal,
            rejected,
        )
    return {"created_links": created_links, "touched_links": touched_links, "rejected": rejected}


def refresh_pubmed_journal_cache(*, watched_journals=None, from_month=None, to_month=None, client=None):
    watched_journals = list(watched_journals or WatchedJournal.objects.filter(active=True).order_by("name", "pk"))
    if from_month is None or to_month is None:
        from_month, to_month = default_pubmed_cache_window()
    client = client or build_pubmed_client()
    seen_pmids = set()
    seen_dois = set()
    totals = {
        "journal_count": len(watched_journals),
        "created_links": 0,
        "touched_links": 0,
    }

    for watched_journal in watched_journals:
        if watched_journal.source == WatchedJournal.Source.CROSSREF:
            stats = refresh_crossref_journal_cache(
                watched_journal,
                from_month,
                to_month,
                seen_dois=seen_dois,
            )
        else:
            stats = refresh_watched_journal_cache(
                watched_journal,
                from_month,
                to_month,
                client=client,
                seen_pmids=seen_pmids,
            )
        totals["created_links"] += stats["created_links"]
        totals["touched_links"] += stats["touched_links"]

    return totals


def populate_pubmed_batch_from_cache(batch, watched_journals):
    watched_journals = list(watched_journals)
    batch.batch_articles.all().delete()
    seen_article_ids = set()
    new_rows = []

    for watched_journal in watched_journals:
        journal_links = (
            WatchedJournalArticle.objects.filter(
                watched_journal=watched_journal,
                publication_month__gte=batch.from_month,
                publication_month__lte=batch.to_month,
            )
            .select_related("article")
            .order_by("-article__publication_date", "-article__publication_month", "article__title", "pk")
        )
        for journal_link in journal_links:
            if journal_link.article_id in seen_article_ids:
                continue
            seen_article_ids.add(journal_link.article_id)
            new_rows.append(
                PubmedBatchArticle(
                    batch=batch,
                    article=journal_link.article,
                    watched_journal=watched_journal,
                    issue=batch.issue,
                )
            )

    if new_rows:
        PubmedBatchArticle.objects.bulk_create(new_rows)

    batch.result_count = len(new_rows)
    batch.selected_count = 0
    batch.save(update_fields=["result_count", "selected_count", "modified"])
    return batch.result_count


def refresh_batch_from_cache(batch, watched_journals, *, refresh_cache=True):
    watched_journals = list(watched_journals)
    if refresh_cache:
        refresh_pubmed_journal_cache(
            watched_journals=watched_journals,
            from_month=batch.from_month,
            to_month=batch.to_month,
        )
    return populate_pubmed_batch_from_cache(batch, watched_journals)


def _adopt_pmid(article, pmid):
    """Set article.pmid if it doesn't conflict with an existing article. Returns True on success."""
    if article.pmid:
        return True
    if PubmedArticle.objects.filter(pmid=pmid).exclude(pk=article.pk).exists():
        logger.warning("Skipping article %d: PMID %s already belongs to another article", article.pk, pmid)
        return False
    article.pmid = pmid
    article.save(update_fields=["pmid"])
    return True


def _adopt_doi(article, doi):
    """Set article.doi if it's currently empty."""
    if article.doi or not doi:
        return
    article.doi = doi.lower()
    article.save(update_fields=["doi"])


def _article_text_sources(article):
    """Combine citation, URL, and pubmed_url fields for identifier extraction."""
    return " ".join(
        filter(
            None,
            [
                getattr(article, "citation", "") or "",
                article.article_url or "",
                article.pubmed_url or "",
            ],
        )
    )


def backfill_article_metadata(*, queryset=None, client=None, batch_size=50, dry_run=False):
    """Re-fetch metadata from PubMed (and CrossRef) for articles missing citation fields.

    Strategy:
    1. Extract PMIDs from citation/URL text for articles that lack one.
    2. Batch-fetch all articles with a PMID (from DB or extracted) via efetch.
    3. Search PubMed individually for remaining articles (by DOI, then title).
    4. For anything still unresolved with a DOI, fall back to CrossRef.
    5. Adopt DOIs extracted from citations/URLs even when no metadata source matches.

    Returns a dict of counts: checked, updated, skipped, failed, crossref_updated.
    """
    client = client or build_pubmed_client()

    if queryset is None:
        queryset = PubmedArticle.objects.all()

    # Find articles missing author data (the most reliable indicator of incomplete metadata)
    articles = list(queryset.order_by("pk"))
    incomplete = []
    for article in articles:
        meta = article.metadata_json or {}
        if not meta.get("authors"):
            incomplete.append(article)

    stats = {"checked": len(incomplete), "updated": 0, "skipped": 0, "failed": 0, "crossref_updated": 0}

    # Pre-extract identifiers from citation/URL fields
    for article in incomplete:
        text = _article_text_sources(article)
        if not article.pmid:
            extracted_pmid = _extract_pmid(text)
            if extracted_pmid:
                article._extracted_pmid = extracted_pmid
        if not article.doi:
            extracted_doi = _extract_doi(text)
            if extracted_doi:
                article._extracted_doi = extracted_doi

    # ── Phase 1: Batch-fetch articles with PMIDs ──────────────────────
    with_pmid = []
    without_pmid = []
    for a in incomplete:
        resolved_pmid = a.pmid or getattr(a, "_extracted_pmid", "")
        if resolved_pmid:
            with_pmid.append((resolved_pmid, a))
        else:
            without_pmid.append(a)

    pmid_to_articles = {}
    for pmid, article in with_pmid:
        pmid_to_articles.setdefault(pmid, []).append(article)

    still_need_metadata = []  # articles not resolved after PubMed phases

    for i in range(0, len(pmid_to_articles), batch_size):
        batch_pmids = list(pmid_to_articles.keys())[i : i + batch_size]
        try:
            payloads = client.fetch_articles(batch_pmids)
        except Exception:
            logger.exception("Failed to fetch batch starting at index %d", i)
            stats["failed"] += len(batch_pmids)
            continue

        fetched_pmids = set()
        for payload in payloads:
            pmid = (payload.get("pmid") or "").strip()
            if pmid and pmid in pmid_to_articles:
                fetched_pmids.add(pmid)
                for article in pmid_to_articles[pmid]:
                    if not dry_run:
                        if not _adopt_pmid(article, pmid):
                            stats["skipped"] += 1
                            continue
                        fill_missing_article_metadata(article, payload)
                    stats["updated"] += 1

        for pmid in batch_pmids:
            if pmid not in fetched_pmids:
                still_need_metadata.extend(pmid_to_articles[pmid])

    # ── Phase 2: Individual PubMed search for articles without PMIDs ──
    for article in without_pmid:
        try:
            payloads = _search_article_on_pubmed(article, client)
        except Exception:
            logger.exception("Failed to search for article %d", article.pk)
            stats["failed"] += 1
            still_need_metadata.append(article)
            continue

        if not payloads:
            still_need_metadata.append(article)
            continue

        payload = payloads[0]
        if not dry_run:
            incoming_pmid = (payload.get("pmid") or "").strip()
            if incoming_pmid and not _adopt_pmid(article, incoming_pmid):
                stats["skipped"] += 1
                continue
            fill_missing_article_metadata(article, payload)
        stats["updated"] += 1

    # ── Phase 3: CrossRef fallback for articles with a DOI ────────────
    for article in still_need_metadata:
        doi = article.doi or getattr(article, "_extracted_doi", "")
        if not doi:
            stats["skipped"] += 1
            continue

        try:
            payload = fetch_crossref_metadata(doi)
        except Exception:
            logger.exception("CrossRef lookup failed for article %d (DOI %s)", article.pk, doi)
            stats["failed"] += 1
            continue

        if not payload:
            stats["skipped"] += 1
            continue

        if not dry_run:
            _adopt_doi(article, doi)
            fill_missing_article_metadata(article, payload)
        stats["updated"] += 1
        stats["crossref_updated"] += 1

    return stats


def _normalise_title(title):
    """Lowercase, replace punctuation with spaces, and collapse whitespace for fuzzy title comparison."""
    import re

    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return text.strip()


def _extract_doi(text):
    """Extract a DOI from a citation string or URL."""
    import re

    match = re.search(r"10\.\d{4,9}/[^\s,;\"'>]+", text or "")
    if match:
        # Strip trailing punctuation
        doi = match.group(0).rstrip(".")
        return doi
    return ""


def _extract_pmid(text):
    """Extract a PMID from a citation string or URL."""
    import re

    # "PMID: 12345678" or "PMID:12345678"
    match = re.search(r"PMID:\s*(\d+)", text or "")
    if match:
        return match.group(1)
    # pmid/12345678 in URLs
    match = re.search(r"pmid/(\d+)", text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _search_article_on_pubmed(article, client):
    """Try to find an article on PubMed by PMID, DOI, then by title.

    Extracts identifiers from the article's doi, citation, article_url, and
    pubmed_url fields before falling back to title search.

    Returns a list of parsed article payloads (empty if no confident match).
    """
    # Gather text sources for identifier extraction
    text_sources = " ".join(
        filter(
            None,
            [
                article.doi or "",
                getattr(article, "citation", "") or "",
                article.article_url or "",
                article.pubmed_url or "",
            ],
        )
    )

    # Try PMID first — most reliable
    pmid = _extract_pmid(text_sources)
    if pmid:
        results = client.find_articles(pmid, retmax=1)
        if results:
            return results

    # Try DOI
    doi = article.doi or _extract_doi(text_sources)
    if doi:
        results = client.find_articles(doi, retmax=2)
        if len(results) == 1:
            return results

    if not article.title:
        return []

    # Try [Title] field search with quoted phrase
    search_term = f'"{article.title}"[Title]'
    results = client.find_articles(search_term, retmax=2)
    if len(results) == 1:
        return results

    # Fall back to free-text search and verify the title matches
    results = client.find_articles(article.title, retmax=5)
    normalised = _normalise_title(article.title)
    matches = [r for r in results if _normalise_title(r.get("title", "")) == normalised]
    if len(matches) == 1:
        return matches

    return []
