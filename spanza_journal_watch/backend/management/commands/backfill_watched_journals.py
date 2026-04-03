"""
Backfill watched journal metadata from NLM catalog and remove mismatched article links.

Usage:
    # Dry run (default) — show what would change without modifying the database
    python manage.py backfill_watched_journals

    # Apply changes
    python manage.py backfill_watched_journals --apply

    # Only process specific journals
    python manage.py backfill_watched_journals --apply --journal 3 --journal 5
"""

from django.core.management.base import BaseCommand

from spanza_journal_watch.backend.models import WatchedJournal, WatchedJournalArticle
from spanza_journal_watch.backend.pubmed_cache import (
    article_matches_journal,
    build_accepted_journal_names,
    build_pubmed_client,
)


class Command(BaseCommand):
    help = "Backfill watched journal identifiers from NLM catalog and remove mismatched article links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Apply changes. Without this flag, runs in dry-run mode.",
        )
        parser.add_argument(
            "--journal",
            dest="journals",
            action="append",
            default=[],
            help="Watched journal PK to process. Can be passed multiple times. Default: all.",
        )
        parser.add_argument(
            "--skip-metadata",
            action="store_true",
            default=False,
            help="Skip NLM catalog metadata backfill, only clean up mismatched articles.",
        )
        parser.add_argument(
            "--skip-cleanup",
            action="store_true",
            default=False,
            help="Skip mismatched article cleanup, only backfill metadata.",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        skip_metadata = options["skip_metadata"]
        skip_cleanup = options["skip_cleanup"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made. Pass --apply to commit.\n"))

        watched_journals = WatchedJournal.objects.all().order_by("name", "pk")
        requested_ids = [int(pk) for pk in options.get("journals") or [] if str(pk).isdigit()]
        if requested_ids:
            watched_journals = watched_journals.filter(pk__in=requested_ids)

        journals = list(watched_journals)
        if not journals:
            self.stdout.write("No watched journals found.")
            return

        if not skip_metadata:
            self._backfill_metadata(journals, dry_run)

        if not skip_cleanup:
            self._cleanup_mismatched(journals, dry_run)

    def _backfill_metadata(self, journals, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("\n── Backfill NLM metadata ──"))
        client = build_pubmed_client()
        updated = 0

        for wj in journals:
            if wj.medline_ta and wj.nlm_id:
                self.stdout.write(f"  {wj.name}: already has medline_ta={wj.medline_ta!r}, nlm_id={wj.nlm_id!r}")
                continue

            # Search NLM catalog by ISSN (most reliable for flagship journals)
            issn = wj.issn_print or wj.issn_electronic
            match = None

            if issn:
                match = self._search_nlm_by_issn(client, issn)

            if not match:
                # Fall back to name-based search
                match = self._search_nlm_by_name(client, wj.name)

            if not match:
                self.stdout.write(self.style.WARNING(f"  {wj.name}: no NLM catalog match found"))
                continue

            changes = []
            if not wj.medline_ta and match.get("medline_ta"):
                changes.append(f"medline_ta={match['medline_ta']!r}")
            if not wj.nlm_id and match.get("nlm_id"):
                changes.append(f"nlm_id={match['nlm_id']!r}")
            if not wj.iso_abbreviation and match.get("iso_abbreviation"):
                changes.append(f"iso_abbreviation={match['iso_abbreviation']!r}")

            if not changes:
                self.stdout.write(f"  {wj.name}: NLM match found but no new fields to fill")
                continue

            if not dry_run:
                update_fields = ["modified"]
                if not wj.medline_ta and match.get("medline_ta"):
                    wj.medline_ta = match["medline_ta"]
                    update_fields.append("medline_ta")
                if not wj.nlm_id and match.get("nlm_id"):
                    wj.nlm_id = match["nlm_id"]
                    update_fields.append("nlm_id")
                if not wj.iso_abbreviation and match.get("iso_abbreviation"):
                    wj.iso_abbreviation = match["iso_abbreviation"]
                    update_fields.append("iso_abbreviation")
                wj.save(update_fields=update_fields)

            prefix = "WOULD SET" if dry_run else "SET"
            self.stdout.write(self.style.SUCCESS(f"  {wj.name}: {prefix} {', '.join(changes)}"))
            updated += 1

        self.stdout.write(f"\n  {'Would update' if dry_run else 'Updated'} {updated} journal(s).")

    def _search_nlm_by_issn(self, client, issn):
        data = client._request_json(
            "esearch.fcgi",
            {"db": "nlmcatalog", "retmode": "json", "retmax": 3, "term": f"{issn}[ISSN]"},
        )
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None

        root = client._request_xml(
            "efetch.fcgi",
            {"db": "nlmcatalog", "retmode": "xml", "id": ",".join(ids)},
        )
        for record in root.findall(".//NLMCatalogRecord"):
            parsed = client._parse_nlm_catalog_record(record)
            if parsed and (parsed.get("issn_print") == issn or parsed.get("issn_electronic") == issn):
                return parsed
        return None

    def _search_nlm_by_name(self, client, name):
        results = client.search_journals(name, retmax=5)
        name_lower = name.lower().strip()
        for r in results:
            r_name = (r.get("name") or "").lower().strip().rstrip(".")
            r_ta = (r.get("medline_ta") or "").lower().strip()
            if r_name == name_lower or r_ta == name_lower:
                return r
        return None

    def _cleanup_mismatched(self, journals, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING("\n── Clean up mismatched article links ──"))
        total_removed = 0

        for wj in journals:
            accepted = build_accepted_journal_names(wj)
            if not accepted:
                self.stdout.write(f"  {wj.name}: no accepted names to validate against, skipping")
                continue

            links = WatchedJournalArticle.objects.filter(watched_journal=wj).select_related("article")
            mismatched_pks = []
            sample_names = set()

            for link in links:
                payload = {
                    "source_journal_name": link.article.source_journal_name,
                    "metadata_json": link.article.metadata_json or {},
                }
                if not article_matches_journal(payload, accepted):
                    mismatched_pks.append(link.pk)
                    source = link.article.source_journal_name
                    if source and len(sample_names) < 5:
                        sample_names.add(source)

            if not mismatched_pks:
                self.stdout.write(f"  {wj.name}: {links.count()} links, all match")
                continue

            if not dry_run:
                WatchedJournalArticle.objects.filter(pk__in=mismatched_pks).delete()

            prefix = "WOULD REMOVE" if dry_run else "REMOVED"
            samples = ", ".join(sorted(sample_names))
            self.stdout.write(
                self.style.WARNING(
                    f"  {wj.name}: {prefix} {len(mismatched_pks)}/{links.count()} mismatched links"
                    f" (e.g. {samples})"
                )
            )
            total_removed += len(mismatched_pks)

        self.stdout.write(f"\n  {'Would remove' if dry_run else 'Removed'} {total_removed} mismatched link(s) total.")
