from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # required for CREATE INDEX CONCURRENTLY

    dependencies = [
        ("backend", "0050_subscribercsv_task_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS backend_pa_title_trgm
                ON backend_pubmedarticle USING gin (title gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX IF EXISTS backend_pa_title_trgm;",
        ),
    ]
