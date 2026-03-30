import datetime

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
from .pubmed import PubmedClient


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
    issn_terms = []
    if watched_journal.issn_print:
        issn_terms.append(f'"{watched_journal.issn_print.strip()}"[ISSN]')
    if watched_journal.issn_electronic:
        issn_terms.append(f'"{watched_journal.issn_electronic.strip()}"[ISSN]')
    if issn_terms:
        return "(" + " OR ".join(issn_terms) + ")"
    return f'"{watched_journal.name.strip()}"[Journal]'


def shift_month(date_value, delta_months):
    month_index = (date_value.year * 12 + (date_value.month - 1)) + int(delta_months)
    year = month_index // 12
    month = (month_index % 12) + 1
    return datetime.date(year, month, 1)


def default_pubmed_cache_window(anchor_date=None):
    anchor = (anchor_date or timezone.now().date()).replace(day=1)
    return shift_month(anchor, -3), anchor


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
        for key in ("mesh_terms", "keywords", "publication_types"):
            existing_values = existing_metadata.get(key) or []
            incoming_values = incoming_metadata.get(key) or []
            if not existing_values and incoming_values:
                existing_metadata[key] = incoming_values
                changed = True
        if changed:
            article.metadata_json = existing_metadata

    if changed:
        article.save()


@transaction.atomic
def upsert_pubmed_article(payload):
    pmid = (payload.get("pmid") or "").strip()
    doi = (payload.get("doi") or "").strip().lower() or None
    if not pmid:
        return None

    article = None
    if doi:
        article = PubmedArticle.objects.filter(doi=doi).first()
    if not article:
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
    history = client.search_pmids_history(build_pubmed_term(watched_journal), from_month, to_month)
    now = timezone.now()
    created_links = 0
    touched_links = 0

    for payload in client.fetch_articles_history(history["webenv"], history["query_key"], history["count"]):
        pmid = (payload.get("pmid") or "").strip()
        if not pmid or pmid in seen_pmids:
            continue
        seen_pmids.add(pmid)

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

    return {"created_links": created_links, "touched_links": touched_links}


def refresh_pubmed_journal_cache(*, watched_journals=None, from_month=None, to_month=None, client=None):
    watched_journals = list(watched_journals or WatchedJournal.objects.filter(active=True).order_by("name", "pk"))
    if from_month is None or to_month is None:
        from_month, to_month = default_pubmed_cache_window()
    client = client or build_pubmed_client()
    seen_pmids = set()
    totals = {
        "journal_count": len(watched_journals),
        "created_links": 0,
        "touched_links": 0,
    }

    for watched_journal in watched_journals:
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
