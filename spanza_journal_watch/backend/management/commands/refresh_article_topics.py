"""Recompute PubmedArticle.topics for every article, after a term list changes."""

from django.core.management.base import BaseCommand

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.backend.topics import article_topics


class Command(BaseCommand):
    help = "Recompute the stored topic flags for every cached article."

    def handle(self, *args, **options):
        changed = 0
        batch = []
        for article in PubmedArticle.objects.only("id", "title", "abstract", "metadata_json", "topics").iterator(
            chunk_size=500
        ):
            topics = article_topics(article)
            if topics != article.topics:
                article.topics = topics
                batch.append(article)
                changed += 1
            if len(batch) >= 500:
                PubmedArticle.objects.bulk_update(batch, ["topics"])
                batch = []
        if batch:
            PubmedArticle.objects.bulk_update(batch, ["topics"])
        self.stdout.write(f"Updated topics on {changed} article(s).")
