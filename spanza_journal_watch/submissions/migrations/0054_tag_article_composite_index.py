from django.db import migrations


class Migration(migrations.Migration):

    atomic = False  # required for CREATE INDEX CONCURRENTLY

    dependencies = [
        ("submissions", "0053_review_publish_date_index"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS submissions_tag_articles_article_tag_idx
                ON submissions_tag_articles (pubmedarticle_id, tag_id);
            """,
            reverse_sql="DROP INDEX IF EXISTS submissions_tag_articles_article_tag_idx;",
        ),
    ]
