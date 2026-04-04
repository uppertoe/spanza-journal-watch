from django.core.management.base import BaseCommand

from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.submissions.models import MeshTagMapping


class Command(BaseCommand):
    help = "Auto-tag articles from MeSH terms using MeshTagMapping. Idempotent."

    def handle(self, *args, **options):
        mappings_cache = {m.mesh_term: m.tag_id for m in MeshTagMapping.objects.select_related("tag").all()}
        self.stdout.write(f"Loaded {len(mappings_cache)} MeSH mappings")

        total = 0
        tagged = 0
        skipped = 0

        for article in PubmedArticle.objects.exclude(metadata_json={}).iterator():
            total += 1
            mesh_terms = (article.metadata_json or {}).get("mesh_terms", [])
            if not mesh_terms:
                skipped += 1
                continue
            tag_pks = {mappings_cache[t] for t in mesh_terms if t in mappings_cache}
            if tag_pks:
                article.tags.add(*tag_pks)
                tagged += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done. Processed {total} articles: {tagged} tagged, {skipped} no matching MeSH terms.")
        )
