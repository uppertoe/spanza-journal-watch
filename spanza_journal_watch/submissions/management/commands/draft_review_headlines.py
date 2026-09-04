"""Draft editorial headlines and bottom lines for reviews that have none.

    python manage.py draft_review_headlines            # live reviews missing a headline
    python manage.py draft_review_headlines --limit 5 --dry-run
    python manage.py draft_review_headlines --slug some-review --overwrite

Drafts go into the review's draft fields, which are never rendered publicly.
The chief editor reads and approves them on the backend Headlines page.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from spanza_journal_watch.submissions.headlines import build_client, draft_review_headline
from spanza_journal_watch.submissions.models import Review


class Command(BaseCommand):
    help = "Draft editorial headlines and bottom lines for reviews using Claude."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many reviews (0 = no limit).")
        parser.add_argument("--slug", action="append", default=[], help="Only these review slugs (repeatable).")
        parser.add_argument("--overwrite", action="store_true", help="Redraft reviews that already have a draft.")
        parser.add_argument("--include-inactive", action="store_true", help="Include unpublished reviews.")
        parser.add_argument("--dry-run", action="store_true", help="Print drafts without saving them.")

    def handle(self, *args, **options):
        client = build_client()
        if client is None:
            raise CommandError("ANTHROPIC_API_KEY is not set.")

        qs = Review.objects.select_related("article", "author").order_by("-created")
        if not options["include_inactive"]:
            qs = qs.filter(active=True)
        if options["slug"]:
            qs = qs.filter(slug__in=options["slug"])
        qs = qs.filter(editorial_headline="")
        if not options["overwrite"]:
            qs = qs.filter(draft_headline="")
        if options["limit"]:
            qs = qs[: options["limit"]]

        drafted = 0
        for review in qs:
            draft = draft_review_headline(review, client=client)
            if not draft:
                self.stdout.write(self.style.WARNING(f"  skipped {review.slug}: no draft returned"))
                continue
            drafted += 1
            self.stdout.write(
                f"{review.slug}\n  headline: {draft['headline']}\n  bottom line: {draft['bottom_line']}\n"
            )
            if not options["dry_run"]:
                review.draft_headline = draft["headline"]
                review.draft_bottom_line = draft["bottom_line"]
                review.draft_generated_at = timezone.now()
                review.save(update_fields=["draft_headline", "draft_bottom_line", "draft_generated_at", "modified"])
        self.stdout.write(
            self.style.SUCCESS(f"Drafted {drafted} review(s){' (dry run)' if options['dry_run'] else ''}.")
        )
