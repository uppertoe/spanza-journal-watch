import datetime

from django.core.management.base import BaseCommand, CommandError

from spanza_journal_watch.backend.models import WatchedJournal
from spanza_journal_watch.backend.pubmed_cache import default_pubmed_cache_window, refresh_pubmed_journal_cache


class Command(BaseCommand):
    help = "Backfill cached PubMed journal articles for watched journals over a month range."

    def add_arguments(self, parser):
        parser.add_argument("--from-month", dest="from_month", help="Start month in YYYY-MM format.")
        parser.add_argument("--to-month", dest="to_month", help="End month in YYYY-MM format.")
        parser.add_argument(
            "--journal",
            dest="journals",
            action="append",
            default=[],
            help="Optional watched journal id to backfill. Can be passed multiple times.",
        )

    @staticmethod
    def _parse_month(value, label):
        try:
            return datetime.date.fromisoformat(f"{value}-01")
        except (TypeError, ValueError) as exc:
            raise CommandError(f"Invalid {label}: expected YYYY-MM") from exc

    def handle(self, *args, **options):
        from_month = options.get("from_month")
        to_month = options.get("to_month")
        if from_month and to_month:
            from_month_value = self._parse_month(from_month, "from-month")
            to_month_value = self._parse_month(to_month, "to-month")
        else:
            from_month_value, to_month_value = default_pubmed_cache_window()

        if from_month_value > to_month_value:
            raise CommandError("--from-month must be before or equal to --to-month")

        watched_journals = WatchedJournal.objects.filter(active=True).order_by("name", "pk")
        requested_ids = [int(pk) for pk in options.get("journals") or [] if str(pk).isdigit()]
        if requested_ids:
            watched_journals = watched_journals.filter(pk__in=requested_ids)

        stats = refresh_pubmed_journal_cache(
            watched_journals=list(watched_journals),
            from_month=from_month_value,
            to_month=to_month_value,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Cached PubMed articles for "
                f"{stats['journal_count']} journals, touching {stats['touched_links']} journal/article links "
                f"({stats['created_links']} new) from {from_month_value:%Y-%m} to {to_month_value:%Y-%m}."
            )
        )
