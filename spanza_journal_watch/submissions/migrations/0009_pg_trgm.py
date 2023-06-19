from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0008_article_active"),
    ]

    operations = [
        TrigramExtension(),
    ]