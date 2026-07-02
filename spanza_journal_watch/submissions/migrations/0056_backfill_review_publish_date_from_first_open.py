from django.db import migrations
from django.utils import timezone

# Reviews are usually drafted days before their issue is published, and the old
# save() logic stamped publish_date from the review's *creation* time — so a
# review staged a week early looked "live" a week before readers could see it.
# The first human review_open is a good proxy for when a review actually went
# live, so move publish_date forward to it. Cap the correction: only apply it
# when the gap is small enough to be draft-staging lead, not an analytics-rollout
# artifact (old reviews whose first *tracked* open long postdates real publish).
_MAX_STAGING_LEAD_DAYS = 45


def backfill_publish_dates(apps, schema_editor):
    Review = apps.get_model("submissions", "Review")
    AnalyticsEvent = apps.get_model("analytics", "AnalyticsEvent")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        review_ct = ContentType.objects.get(app_label="submissions", model="review")
    except ContentType.DoesNotExist:
        return

    reviews = Review.objects.filter(active=True, publish_date__isnull=False).only("id", "publish_date")
    for review in reviews.iterator():
        first_open = (
            AnalyticsEvent.objects.filter(
                content_type=review_ct,
                object_id=review.id,
                event_type="review_open",
                automated=False,
            )
            .order_by("timestamp")
            .values_list("timestamp", flat=True)
            .first()
        )
        if first_open is None:
            continue
        first_open_date = timezone.localtime(first_open).date() if timezone.is_aware(first_open) else first_open.date()
        delta = (first_open_date - review.publish_date).days
        # Only move the date forward, and only within the staging-lead window.
        if 0 < delta <= _MAX_STAGING_LEAD_DAYS:
            Review.objects.filter(pk=review.id).update(publish_date=first_open_date)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0055_issueslugredirect"),
        ("analytics", "0024_schedule_ua_cohort_downgrade"),
    ]

    operations = [
        # Data-only correction; nothing to reverse safely.
        migrations.RunPython(backfill_publish_dates, migrations.RunPython.noop),
    ]
