import importlib

from django.db import migrations

_hashing = importlib.import_module("spanza_journal_watch.analytics.migrations.0028_hash_session_keys")


def hash_recommendation_session_keys(apps, schema_editor):
    _hashing.hash_session_keys(apps, schema_editor, "backend", "PubmedArticleVisitorRecommendation")


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0060_clear_untracked_fulltext_clicks"),
        ("analytics", "0028_hash_session_keys"),
    ]

    operations = [
        migrations.RunPython(hash_recommendation_session_keys, migrations.RunPython.noop, elidable=True),
    ]
