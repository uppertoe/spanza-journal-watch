from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0017_pubmedbatcharticle_planka_tracking"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="watchedjournal",
            name="pubmed_query_override",
        ),
    ]
