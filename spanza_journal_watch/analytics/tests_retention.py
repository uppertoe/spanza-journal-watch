from datetime import timedelta

import pytest
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.analytics.tasks import purge_expired_personal_records
from spanza_journal_watch.backend.models import EmailThread
from spanza_journal_watch.newsletter.models import Subscriber

pytestmark = pytest.mark.django_db


def test_purge_expired_personal_records_keeps_recent_rows_and_subscribers(settings):
    settings.PERSONAL_DATA_RETENTION_DAYS = 730
    old = timezone.now() - timedelta(days=800)
    subscriber = Subscriber.objects.create(email="left@example.com", subscribed=False)
    old_event = AnalyticsEvent.objects.create(event_type="page_visit", subscriber=subscriber)
    AnalyticsEvent.objects.filter(pk=old_event.pk).update(timestamp=old)
    recent_event = AnalyticsEvent.objects.create(event_type="page_visit")
    EmailThread.objects.create(external_address="old@example.com", subject="Old enquiry", last_message_at=old)
    EmailThread.objects.create(
        external_address="new@example.com", subject="New enquiry", last_message_at=timezone.now()
    )

    result = purge_expired_personal_records()

    assert result["analytics_events"] == 1
    assert result["inbox_threads"] == 1
    assert AnalyticsEvent.objects.filter(pk=recent_event.pk).exists()
    assert list(EmailThread.objects.values_list("subject", flat=True)) == ["New enquiry"]
    # The address is kept so a later import cannot email someone who left
    assert Subscriber.objects.filter(email="left@example.com").exists()
