from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0016_pubmed_article_intake_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="pubmedbatcharticle",
            name="planka_card_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="pubmedbatcharticle",
            name="planka_card_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="pubmedbatcharticle",
            name="planka_pushed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pubmedbatcharticle",
            name="planka_push_error",
            field=models.TextField(blank=True),
        ),
    ]
