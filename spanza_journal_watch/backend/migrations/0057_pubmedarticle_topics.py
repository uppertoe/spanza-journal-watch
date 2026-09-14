import django.contrib.postgres.fields
import django.contrib.postgres.indexes
from django.db import migrations, models


def fill_topics(apps, schema_editor):
    from spanza_journal_watch.backend.topics import compute_topics

    PubmedArticle = apps.get_model("backend", "PubmedArticle")
    batch = []
    for article in PubmedArticle.objects.only("id", "title", "abstract", "metadata_json").iterator(chunk_size=500):
        article.topics = compute_topics(article.title, article.abstract, article.metadata_json)
        batch.append(article)
        if len(batch) >= 500:
            PubmedArticle.objects.bulk_update(batch, ["topics"])
            batch = []
    if batch:
        PubmedArticle.objects.bulk_update(batch, ["topics"])


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0056_pubmedarticle_abstract_trgm_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="pubmedarticle",
            name="topics",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=32), blank=True, default=list, size=None
            ),
        ),
        migrations.RunPython(fill_topics, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="pubmedarticle",
            index=django.contrib.postgres.indexes.GinIndex(fields=["topics"], name="backend_pa_topics_gin"),
        ),
    ]
