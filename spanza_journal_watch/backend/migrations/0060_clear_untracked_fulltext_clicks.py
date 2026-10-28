from django.db import migrations


def clear_untracked_clicks(apps, schema_editor):
    """Remove full-text clicks recorded while the member's CPD tracking was off.

    The CPD setting told these members that clicks were not being tracked.
    """
    PubmedArticleUserState = apps.get_model("backend", "PubmedArticleUserState")
    PubmedArticleUserState.objects.filter(user__cpd_tracking_enabled=False, full_text_clicked_at__isnull=False).update(
        full_text_clicked_at=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("backend", "0059_subscribercsv_content_in_database"),
        ("users", "0006_user_cpd_tracking_enabled_user_is_spanza_member"),
    ]

    operations = [
        migrations.RunPython(clear_untracked_clicks, migrations.RunPython.noop),
    ]
