"""
Backfill Issue.image from the legacy PageHeader/FeatureArticle layout model.

For each Issue that has no direct image but has an associated FeatureArticle
with an image, copies that image file into Issue.image.

Safe to run multiple times — skips issues that already have Issue.image set.

Usage:
    python manage.py backfill_issue_images [--dry-run]
"""

import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from spanza_journal_watch.submissions.models import Issue


class Command(BaseCommand):
    help = "Copy issue images from the layout FeatureArticle model into Issue.image."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be copied without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes will be saved.\n"))

        copied = 0
        skipped = 0
        missing = 0

        for issue in Issue.objects.order_by("pk"):
            if issue.image:
                skipped += 1
                continue

            feature_article = issue.get_header_feature_article()
            if not feature_article or not feature_article.image:
                missing += 1
                self.stdout.write(f"  No layout image: Issue #{issue.pk} — {issue.name}")
                continue

            src = feature_article.image
            try:
                src.open("rb")
                content = src.read()
                src.close()
            except (OSError, ValueError) as exc:
                self.stdout.write(
                    self.style.ERROR(f"  Could not read image for Issue #{issue.pk} ({issue.name}): {exc}")
                )
                missing += 1
                continue

            filename = os.path.basename(src.name)
            self.stdout.write(f"  Copying Issue #{issue.pk} — {issue.name} → issues/{filename}")

            if not dry_run:
                issue.image.save(filename, ContentFile(content), save=True)

            copied += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'Would copy' if dry_run else 'Copied'} {copied} image(s). "
                f"Skipped {skipped} (already had image). "
                f"No source image for {missing}."
            )
        )
