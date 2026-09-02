"""Submit URLs to IndexNow so Bing and other engines re-crawl them promptly.

With no arguments every URL in the public sitemap is submitted (useful once
after configuring INDEXNOW_KEY, or after a bulk content change). Otherwise pass
site-relative paths or absolute URLs.
"""

import requests
from django.core.management.base import BaseCommand, CommandError

from spanza_journal_watch.utils import indexnow


class Command(BaseCommand):
    help = "Submit URLs to IndexNow (all sitemap URLs when no paths are given)."

    def add_arguments(self, parser):
        parser.add_argument(
            "paths", nargs="*", help="Site-relative paths or absolute URLs. Default: the whole sitemap."
        )
        parser.add_argument("--dry-run", action="store_true", help="Print the URLs without submitting them.")

    def handle(self, *args, **options):
        paths = options["paths"] or indexnow.sitemap_paths()
        urls = list(dict.fromkeys(indexnow.absolute_url(path) for path in paths))

        if options["dry_run"]:
            self.stdout.write("\n".join(urls))
            self.stdout.write(self.style.NOTICE(f"{len(urls)} URL(s) would be submitted"))
            return

        if not indexnow.indexnow_enabled():
            raise CommandError("IndexNow is disabled: set INDEXNOW_KEY (and run with DEBUG off).")

        try:
            submitted = indexnow.submit_urls(urls)
        except requests.RequestException as exc:
            raise CommandError(f"IndexNow request failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"IndexNow accepted {submitted} of {len(urls)} URL(s)"))
