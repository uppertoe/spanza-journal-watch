"""
Compute tag co-occurrence clusters from article tagging data.

Builds an adjacency graph of curated tags based on how often they appear
together on articles, then identifies connected components as clusters.
Results are stored in cache for use on the Explore page.
"""

from collections import defaultdict
from itertools import combinations

from django.core.cache import cache
from django.core.management.base import BaseCommand

from spanza_journal_watch.submissions.models import Tag

CACHE_KEY = "tag_clusters"
CACHE_TIMEOUT = 60 * 60 * 24 * 7  # 1 week
SIMILARITY_THRESHOLD = 0.5


class Command(BaseCommand):
    help = "Compute tag co-occurrence clusters and cache results."

    def add_arguments(self, parser):
        parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD, help="Similarity threshold (0-1)")
        parser.add_argument("--dry-run", action="store_true", help="Print clusters without caching")

    def handle(self, *args, **options):
        threshold = options["threshold"]
        dry_run = options["dry_run"]

        # Fetch curated tags and their article sets
        tags = Tag.objects.filter(active=True, curated=True)
        tag_articles = {}
        for tag in tags:
            article_ids = set(tag.articles.values_list("id", flat=True))
            if article_ids:
                tag_articles[tag.id] = article_ids

        self.stdout.write(f"Found {len(tag_articles)} curated tags with articles.")

        # Compute pairwise similarity (overlap / min size)
        adjacency = defaultdict(set)
        pair_count = 0
        for (a_id, a_articles), (b_id, b_articles) in combinations(tag_articles.items(), 2):
            overlap = len(a_articles & b_articles)
            if overlap == 0:
                continue
            min_size = min(len(a_articles), len(b_articles))
            similarity = overlap / min_size
            if similarity >= threshold:
                adjacency[a_id].add(b_id)
                adjacency[b_id].add(a_id)
                pair_count += 1

        self.stdout.write(f"Found {pair_count} tag pairs above threshold {threshold}.")

        # Find connected components via BFS
        visited = set()
        clusters = []
        for tag_id in tag_articles:
            if tag_id in visited:
                continue
            component = set()
            queue = [tag_id]
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                queue.extend(adjacency.get(current, set()) - visited)
            if len(component) > 1:
                clusters.append(sorted(component))

        # Sort clusters by size descending
        clusters.sort(key=len, reverse=True)

        # Print results
        tag_names = {t.id: t.text for t in tags}
        self.stdout.write(f"\n{len(clusters)} clusters found:\n")
        for i, cluster in enumerate(clusters, 1):
            names = [tag_names.get(tid, str(tid)) for tid in cluster]
            self.stdout.write(f"  Cluster {i} ({len(cluster)} tags): {', '.join(names)}")

        # Singletons
        clustered_ids = {tid for c in clusters for tid in c}
        singletons = [tid for tid in tag_articles if tid not in clustered_ids]
        if singletons:
            names = [tag_names.get(tid, str(tid)) for tid in singletons]
            self.stdout.write(f"\n  Unclustered ({len(singletons)}): {', '.join(names)}")

        if not dry_run:
            cache.set(CACHE_KEY, clusters, CACHE_TIMEOUT)
            self.stdout.write(self.style.SUCCESS(f"\nCached {len(clusters)} clusters."))
        else:
            self.stdout.write("\nDry run — not cached.")
