import logging

from django.core.management.base import BaseCommand

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.backend.pubmed_cache import build_pubmed_client, fill_missing_article_metadata

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-fetch PubMed metadata for articles missing MeSH terms, then auto-tag."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200, help="PMIDs per efetch request (max 200)")
        parser.add_argument("--limit", type=int, default=0, help="Max articles to process (0 = all)")
        parser.add_argument("--dry-run", action="store_true", help="Report counts without fetching")

    def handle(self, *args, **options):
        batch_size = min(options["batch_size"], 200)
        limit = options["limit"]
        dry_run = options["dry_run"]

        # Find articles with a PMID but no MeSH terms
        qs = PubmedArticle.objects.filter(pmid__isnull=False)
        candidates = []
        for article in qs.iterator():
            mesh = (article.metadata_json or {}).get("mesh_terms", [])
            if not mesh:
                candidates.append(article)
        if limit:
            candidates = candidates[:limit]

        self.stdout.write(f"Articles with PMID but no MeSH terms: {len(candidates)}")

        if dry_run or not candidates:
            return

        client = build_pubmed_client()
        updated = 0
        still_empty = 0
        errors = 0

        # Batch fetch by PMID
        pmid_to_article = {a.pmid: a for a in candidates}
        pmid_list = list(pmid_to_article.keys())

        for i in range(0, len(pmid_list), batch_size):
            batch = pmid_list[i : i + batch_size]
            try:
                payloads = client.fetch_articles(batch)
            except Exception:
                logger.exception("Failed to fetch batch at offset %d", i)
                errors += len(batch)
                continue

            fetched = {}
            for payload in payloads:
                pmid = (payload.get("pmid") or "").strip()
                if pmid:
                    fetched[pmid] = payload

            for pmid in batch:
                article = pmid_to_article[pmid]
                payload = fetched.get(pmid)
                if not payload:
                    still_empty += 1
                    continue
                incoming_mesh = (payload.get("metadata_json") or {}).get("mesh_terms", [])
                if incoming_mesh:
                    fill_missing_article_metadata(article, payload)
                    # save() triggers auto_tag_from_mesh()
                    updated += 1
                else:
                    still_empty += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done. Updated: {updated}, still no MeSH: {still_empty}, errors: {errors}")
        )
