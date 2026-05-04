import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):
    """
    Sync model state for the GinIndex added to PubmedArticle.Meta.indexes.
    The index itself was already created by 0051 via raw SQL, so the database
    operation is a no-op here — we only update Django's migration state.
    """

    dependencies = [
        ("backend", "0051_pubmedarticle_title_trgm_index"),
        ("submissions", "0054_tag_article_composite_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="pubmedarticle",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["title"], name="backend_pa_title_trgm", opclasses=["gin_trgm_ops"]
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
