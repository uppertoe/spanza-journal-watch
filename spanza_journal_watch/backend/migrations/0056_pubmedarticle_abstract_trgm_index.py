import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):
    """Trigram index on article abstracts, so ILIKE substring search uses it.

    Built CONCURRENTLY so production keeps serving while it is created.
    """

    atomic = False

    dependencies = [
        ("backend", "0055_pubmedarticlevisitorrecommendation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="pubmedarticle",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["abstract"], name="backend_pa_abstract_trgm", opclasses=["gin_trgm_ops"]
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS backend_pa_abstract_trgm
                        ON backend_pubmedarticle USING gin (abstract gin_trgm_ops);
                    """,
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS backend_pa_abstract_trgm;",
                ),
            ],
        ),
    ]
