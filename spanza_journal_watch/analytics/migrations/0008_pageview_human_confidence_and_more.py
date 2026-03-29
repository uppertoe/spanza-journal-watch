from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0007_analyticsevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="pageview",
            name="human_confidence",
            field=models.CharField(
                choices=[
                    ("suspected_automated", "Suspected automated"),
                    ("probable_human", "Probable human"),
                    ("known_subscriber_human", "Known subscriber human"),
                ],
                default="probable_human",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="human_confidence",
            field=models.CharField(
                choices=[
                    ("suspected_automated", "Suspected automated"),
                    ("probable_human", "Probable human"),
                    ("known_subscriber_human", "Known subscriber human"),
                ],
                default="probable_human",
                max_length=32,
            ),
        ),
        migrations.RunSQL(
            sql="""
            UPDATE analytics_pageview
            SET human_confidence = CASE
                WHEN automated THEN 'suspected_automated'
                WHEN subscriber_id IS NOT NULL THEN 'known_subscriber_human'
                ELSE 'probable_human'
            END
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="""
            UPDATE analytics_analyticsevent
            SET human_confidence = CASE
                WHEN automated THEN 'suspected_automated'
                WHEN subscriber_id IS NOT NULL THEN 'known_subscriber_human'
                ELSE 'probable_human'
            END
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
