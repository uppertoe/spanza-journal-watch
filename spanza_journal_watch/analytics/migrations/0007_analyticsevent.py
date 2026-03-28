from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("newsletter", "0014_delete_elementimage"),
        ("analytics", "0006_pageview_automation_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalyticsEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("review_open", "Review open"),
                            ("review_engaged", "Review engaged"),
                            ("review_full_text_click", "Review full text click"),
                            ("review_share_copy_link", "Review shared via copy link"),
                            ("review_share_email", "Review shared via email"),
                            ("review_share_native", "Review shared via native share"),
                            ("review_share_bluesky", "Review shared via Bluesky"),
                            ("review_share_x", "Review shared via X"),
                            ("review_share_facebook", "Review shared via Facebook"),
                            ("search", "Search performed"),
                            ("search_result_click", "Search result clicked"),
                        ],
                        max_length=48,
                    ),
                ),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(blank=True, default="", max_length=64)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("scroll_depth", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("automated", models.BooleanField(default=False)),
                ("session_key", models.CharField(blank=True, default="", max_length=64)),
                (
                    "content_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="contenttypes.contenttype",
                    ),
                ),
                (
                    "subscriber",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="newsletter.subscriber",
                    ),
                ),
            ],
            options={
                "ordering": ("-timestamp",),
            },
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["event_type", "timestamp"], name="analytics_a_event_t_1af153_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["content_type", "object_id", "timestamp"], name="analytics_a_content_49598d_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["source", "timestamp"], name="analytics_a_source_f2dc93_idx"),
        ),
    ]
