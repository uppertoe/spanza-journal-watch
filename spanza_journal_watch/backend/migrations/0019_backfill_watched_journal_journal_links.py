from django.db import migrations
from django.utils.text import slugify


def _unique_slug(Journal, name):
    base_slug = slugify(name) or "journal"
    slug = base_slug
    index = 2
    while Journal.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{index}"
        index += 1
    return slug


def backfill_watched_journal_links(apps, schema_editor):
    WatchedJournal = apps.get_model("backend", "WatchedJournal")
    Journal = apps.get_model("submissions", "Journal")

    for watched in WatchedJournal.objects.filter(journal__isnull=True):
        name = (watched.name or "").strip()
        if not name:
            continue

        journal = Journal.objects.filter(name__iexact=name).order_by("pk").first()
        if journal is None:
            journal = Journal.objects.create(
                name=name,
                slug=_unique_slug(Journal, name),
                active=True,
            )

        watched.journal_id = journal.pk
        watched.save(update_fields=["journal"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0018_remove_watchedjournal_pubmed_query_override"),
        ("submissions", "0037_alter_issue_options"),
    ]

    operations = [
        migrations.RunPython(backfill_watched_journal_links, reverse_code=noop_reverse),
    ]
