"""Match review articles to PubMed records and deduplicate.

Finds PubmedArticle records missing a PMID (crossref imports and manually-created
review stubs), resolves them via PubMed or CrossRef, and — when a canonical record
already exists — merges the stub into it so that reviews, user states, and journal
browser links all point to the right place.

Strategy:
1. Batch DOI → PMID via NCBI ID converter (fast, reliable, no false positives).
2. For articles WITH a DOI that the converter didn't resolve: skip PubMed search
   (these journals aren't PubMed-indexed) and go straight to CrossRef for metadata.
3. For articles WITHOUT a DOI: search PubMed by title (these are manual review
   stubs where a match is plausible).
4. Deduplicate: if a resolved PMID/DOI already belongs to another PubmedArticle,
   merge the stub into the canonical record.
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from spanza_journal_watch.backend.models import (
    PubmedArticle,
    PubmedArticleUserState,
    PubmedBatchArticle,
    WatchedJournalArticle,
)
from spanza_journal_watch.backend.pubmed import fetch_crossref_metadata
from spanza_journal_watch.backend.pubmed_cache import (
    _search_article_on_pubmed,
    build_pubmed_client,
    fill_missing_article_metadata,
)
from spanza_journal_watch.submissions.models import Comment, Review

logger = logging.getLogger(__name__)


def _convert_dois_to_pmids(dois):
    """Use NCBI ID converter to batch-convert DOIs to PMIDs.

    Returns a dict mapping DOI (lowercase) → PMID string.
    """
    import json
    import urllib.parse
    import urllib.request

    if not dois:
        return {}

    results = {}
    batch_size = 100
    doi_list = list(dois)

    for i in range(0, len(doi_list), batch_size):
        batch = doi_list[i : i + batch_size]
        ids_param = ",".join(batch)
        url = (
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
            f"?ids={urllib.parse.quote(ids_param)}&format=json&tool=spanza-journal-watch"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "spanza-journal-watch/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for record in data.get("records", []):
                doi = (record.get("doi") or "").strip().lower()
                pmid = (record.get("pmid") or "").strip()
                if doi and pmid:
                    results[doi] = pmid
        except Exception:
            logger.exception("NCBI ID converter failed for batch starting at index %d", i)

    return results


@transaction.atomic
def _merge_article(stub, canonical, *, dry_run=False):
    """Move all references from stub to canonical, then delete stub."""
    moves = []

    for review in Review.objects.filter(article=stub):
        moves.append(f"  Review {review.pk} ({review.slug})")
        if not dry_run:
            review.article = canonical
            review.save(update_fields=["article"])

    for comment in Comment.objects.filter(article=stub):
        moves.append(f"  Comment {comment.pk}")
        if not dry_run:
            comment.article = canonical
            comment.save(update_fields=["article"])

    for state in PubmedArticleUserState.objects.filter(article=stub):
        if PubmedArticleUserState.objects.filter(article=canonical, user=state.user).exists():
            moves.append(f"  UserState {state.pk} (user {state.user}) — skipped, canonical already has state")
            if not dry_run:
                state.delete()
        else:
            moves.append(f"  UserState {state.pk} (user {state.user})")
            if not dry_run:
                state.article = canonical
                state.save(update_fields=["article"])

    for link in WatchedJournalArticle.objects.filter(article=stub):
        if WatchedJournalArticle.objects.filter(article=canonical, watched_journal=link.watched_journal).exists():
            moves.append(f"  WatchedJournalArticle {link.pk} — skipped, canonical already linked")
            if not dry_run:
                link.delete()
        else:
            moves.append(f"  WatchedJournalArticle {link.pk}")
            if not dry_run:
                link.article = canonical
                link.save(update_fields=["article"])

    for ba in PubmedBatchArticle.objects.filter(article=stub):
        if PubmedBatchArticle.objects.filter(article=canonical, batch=ba.batch).exists():
            moves.append(f"  PubmedBatchArticle {ba.pk} — skipped, canonical already in batch")
            if not dry_run:
                ba.delete()
        else:
            moves.append(f"  PubmedBatchArticle {ba.pk}")
            if not dry_run:
                ba.article = canonical
                ba.save(update_fields=["article"])

    if not dry_run:
        stub.delete()

    return moves


class Command(BaseCommand):
    help = "Match PubmedArticles missing PMIDs to PubMed records and deduplicate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes. Default is dry run.",
        )
        parser.add_argument(
            "--reviews-only",
            action="store_true",
            help="Only process articles linked to reviews (skip crossref-only articles).",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        reviews_only = options["reviews_only"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — pass --apply to write changes\n"))

        # Find articles missing PMID
        missing_pmid = PubmedArticle.objects.filter(Q(pmid__isnull=True) | Q(pmid=""))
        if reviews_only:
            review_article_ids = Review.objects.filter(active=True).values_list("article_id", flat=True)
            missing_pmid = missing_pmid.filter(pk__in=review_article_ids)

        articles = list(missing_pmid.order_by("pk"))
        self.stdout.write(f"Found {len(articles)} article(s) missing PMID\n")

        if not articles:
            return

        with_doi = {a.doi.strip().lower(): a for a in articles if a.doi}
        without_doi = [a for a in articles if not a.doi]
        self.stdout.write(f"  {len(with_doi)} with DOI, {len(without_doi)} without DOI\n")

        # ── Phase 1: Batch DOI → PMID via NCBI ID converter ────────────
        doi_to_pmid = {}
        if with_doi:
            self.stdout.write("Phase 1: Converting DOIs to PMIDs via NCBI ID converter...")
            doi_to_pmid = _convert_dois_to_pmids(list(with_doi.keys()))
            self.stdout.write(f" {len(doi_to_pmid)} resolved\n")

        # ── Phase 2: PubMed search for articles WITHOUT a DOI ──────────
        # Articles with a DOI that didn't resolve via the converter are from
        # journals not indexed in PubMed — skip individual search (slow and
        # prone to false title matches) and go straight to CrossRef.
        search_results = {}
        if without_doi:
            client = build_pubmed_client()
            self.stdout.write(f"Phase 2: Searching PubMed for {len(without_doi)} article(s) without DOI...\n")
            for article in without_doi:
                try:
                    payloads = _search_article_on_pubmed(article, client)
                except Exception:
                    logger.exception("PubMed search failed for article %d", article.pk)
                    continue
                if payloads:
                    payload = payloads[0]
                    pmid = (payload.get("pmid") or "").strip()
                    if pmid:
                        search_results[article.pk] = payload

        # ── Phase 3: CrossRef metadata for unresolved DOI articles ─────
        unresolved_dois = set(with_doi.keys()) - set(doi_to_pmid.keys())
        crossref_results = {}
        if unresolved_dois:
            self.stdout.write(
                f"Phase 3: Fetching CrossRef metadata for {len(unresolved_dois)} " f"article(s) not in PubMed...\n"
            )
            for doi in unresolved_dois:
                try:
                    payload = fetch_crossref_metadata(doi)
                except Exception:
                    logger.exception("CrossRef lookup failed for DOI %s", doi)
                    continue
                if payload:
                    crossref_results[doi] = payload

        # ── Phase 4: Process results ───────────────────────────────────
        stats = {
            "pubmed_merged": 0,
            "pubmed_backfilled": 0,
            "crossref_backfilled": 0,
            "unresolved": 0,
            "failed": 0,
        }

        for article in articles:
            doi = (article.doi or "").strip().lower()
            pmid = doi_to_pmid.get(doi, "")
            pubmed_payload = search_results.get(article.pk)
            crossref_payload = crossref_results.get(doi)

            # If no PMID from converter, try from PubMed search payload
            if not pmid and pubmed_payload:
                pmid = (pubmed_payload.get("pmid") or "").strip()

            if pmid:
                # ── PubMed match: merge or backfill ────────────────
                canonical = PubmedArticle.objects.filter(pmid=pmid).exclude(pk=article.pk).first()
                if not canonical and doi:
                    canonical = PubmedArticle.objects.filter(doi=doi).exclude(pk=article.pk).first()

                if canonical:
                    self.stdout.write(
                        f"  MERGE  PK={article.pk} → PK={canonical.pk} (PMID {pmid}) " f"title={article.title[:50]}\n"
                    )
                    moves = _merge_article(article, canonical, dry_run=dry_run)
                    for line in moves:
                        self.stdout.write(f"    {line}\n")
                    stats["pubmed_merged"] += 1
                else:
                    self.stdout.write(f"  PUBMED  PK={article.pk} PMID={pmid} " f"title={article.title[:50]}\n")
                    if not dry_run:
                        article.pmid = pmid
                        article.save(update_fields=["pmid"])
                        if pubmed_payload:
                            fill_missing_article_metadata(article, pubmed_payload)
                        else:
                            try:
                                client = client if "client" in dir() else build_pubmed_client()
                                payloads = client.fetch_articles([pmid])
                                if payloads:
                                    fill_missing_article_metadata(article, payloads[0])
                            except Exception:
                                logger.exception("Failed to fetch metadata for PMID %s", pmid)
                                stats["failed"] += 1
                                continue
                    stats["pubmed_backfilled"] += 1

            elif crossref_payload:
                # ── CrossRef-only: backfill metadata ───────────────
                self.stdout.write(f"  CROSSREF  PK={article.pk} doi={doi} " f"title={article.title[:50]}\n")
                if not dry_run:
                    fill_missing_article_metadata(article, crossref_payload)
                stats["crossref_backfilled"] += 1

            else:
                stats["unresolved"] += 1
                self.stdout.write(f"  UNRESOLVED  PK={article.pk} doi={doi or '—'} " f"title={article.title[:60]}\n")

        self.stdout.write("\n")
        total_resolved = stats["pubmed_merged"] + stats["pubmed_backfilled"] + stats["crossref_backfilled"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {total_resolved} resolved "
                f"({stats['pubmed_merged']} merged, "
                f"{stats['pubmed_backfilled']} PubMed backfilled, "
                f"{stats['crossref_backfilled']} CrossRef backfilled), "
                f"{stats['unresolved']} unresolved, {stats['failed']} failed"
            )
        )
