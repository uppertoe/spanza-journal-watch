from django.db import migrations, models
from django.db.models import Min, Q


def backfill_published_at(apps, schema_editor):
    """Live issues take their first newsletter send date, or failing that, the date they were created."""
    Issue = apps.get_model("submissions", "Issue")
    issues = Issue.objects.filter(active=True, published_at__isnull=True).annotate(
        first_send=Min(
            "newsletter__send_date",
            filter=Q(newsletter__is_sent=True, newsletter__send_date__isnull=False),
        )
    )
    for issue in issues:
        Issue.objects.filter(pk=issue.pk).update(published_at=issue.first_send or issue.created)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0061_tag_name_introduction"),
        ("newsletter", "0023_subscriber_unsubscribed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="published_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(backfill_published_at, migrations.RunPython.noop),
    ]
