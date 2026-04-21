from django.db import migrations


def backfill_journal_fk(apps, schema_editor):
    PubmedArticle = apps.get_model("backend", "PubmedArticle")
    Journal = apps.get_model("submissions", "Journal")
    from django.utils.text import slugify

    def make_unique_slug(name):
        base = slugify(name) or "journal"
        slug = base
        n = 2
        while Journal.objects.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    # Cache existing journals by lower(name) to avoid creating duplicates
    # that only differ in whitespace or case.
    existing = {j.name.strip().lower(): j for j in Journal.objects.all()}

    to_process = PubmedArticle.objects.filter(journal__isnull=True).exclude(source_journal_name="")
    for article in to_process.iterator():
        name = (article.source_journal_name or "").strip()
        if not name:
            continue
        key = name.lower()
        journal = existing.get(key)
        if journal is None:
            # Historical Journal.save() auto-slugs, but data migrations
            # bypass model methods, so compute the slug inline.
            journal = Journal.objects.create(name=name, slug=make_unique_slug(name), active=True)
            existing[key] = journal
        article.journal = journal
        article.save(update_fields=["journal"])


def noop_reverse(apps, schema_editor):
    # Leave backfilled FKs in place on reverse — removing them would lose
    # data and any Journals we created during the forward run may now be
    # referenced by other rows.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0048_add_inbox_search_indexes"),
        ("submissions", "0053_review_publish_date_index"),
    ]

    operations = [
        migrations.RunPython(backfill_journal_fk, noop_reverse),
    ]
