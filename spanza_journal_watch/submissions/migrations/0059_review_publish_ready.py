from django.db import migrations, models


def mark_published_reviews_ready(apps, schema_editor):
    Review = apps.get_model("submissions", "Review")
    Review.objects.filter(active=True).update(publish_ready=True)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0058_review_headline_drafts"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="publish_ready",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_published_reviews_ready, migrations.RunPython.noop),
    ]
