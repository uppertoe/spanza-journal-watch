from django.core.management.base import BaseCommand

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.backend.pubmed_cache import backfill_article_metadata


class Command(BaseCommand):
    help = "Re-fetch metadata from PubMed for articles missing citation fields (authors, volume, etc)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be updated without writing changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of articles to process (0 = all).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of PMIDs to fetch per API call (default 50).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        batch_size = options["batch_size"]

        queryset = PubmedArticle.objects.all()
        if limit:
            queryset = queryset[:limit]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written"))

        stats = backfill_article_metadata(
            queryset=queryset,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        crossref = stats.get("crossref_updated", 0)
        crossref_note = f" ({crossref} via CrossRef)" if crossref else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {stats['checked']} articles: "
                f"{stats['updated']} updated{crossref_note}, "
                f"{stats['skipped']} skipped, "
                f"{stats['failed']} failed."
            )
        )
