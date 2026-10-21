from django.db import migrations

from spanza_journal_watch.utils.functions import split_honorific


def move_honorifics_into_title(apps, schema_editor):
    Author = apps.get_model("submissions", "Author")
    for author in Author.objects.all():
        honorific, name = split_honorific(author.name)
        if honorific:
            Author.objects.filter(pk=author.pk).update(title=honorific, name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0059_review_publish_ready"),
    ]

    operations = [
        migrations.RunPython(move_honorifics_into_title, migrations.RunPython.noop),
    ]
