"""
Tests for subscriber acquisition analytics (source field + dashboard helpers).
"""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from spanza_journal_watch.backend.analytics_views import (
    _acquisition_summary,
    _recent_subscriber_feed,
)
from spanza_journal_watch.backend.models import SubscriberCSV
from spanza_journal_watch.backend.tasks import process_subscriber_csv
from spanza_journal_watch.newsletter.models import Subscriber

User = get_user_model()
pytestmark = pytest.mark.django_db


def _window():
    now = timezone.now()
    return now - datetime.timedelta(days=1), now + datetime.timedelta(days=1)


class TestSourcePopulation:
    def test_drawer_subscribe_sets_source_drawer(self, client):
        client.post(
            "/newsletter/subscribe?source=drawer",
            {"email": "drawer-src@example.com", "source": "drawer"},
            HTTP_HX_REQUEST="true",
        )
        sub = Subscriber.objects.get(email="drawer-src@example.com")
        assert sub.source == Subscriber.Source.DRAWER

    def test_form_subscribe_sets_source_subscribe_form(self, client):
        client.post(
            "/newsletter/subscribe",
            {"email": "form-src@example.com"},
            HTTP_HX_REQUEST="true",
        )
        sub = Subscriber.objects.get(email="form-src@example.com")
        assert sub.source == Subscriber.Source.SUBSCRIBE_FORM

    def test_toggle_subscription_creates_with_user_signup(self, client):
        user = User.objects.create_user(email="signup@example.com", password="pw")
        client.force_login(user)
        client.post("/newsletter/toggle", HTTP_HX_REQUEST="true")
        sub = Subscriber.objects.get(email="signup@example.com")
        assert sub.source == Subscriber.Source.USER_SIGNUP

    def test_csv_import_sets_source_csv_import(self, tmp_path):
        csv_path = tmp_path / "batch.csv"
        csv_path.write_text("a@example.com\nb@example.com\n")
        csv = SubscriberCSV.objects.create(
            name="batch.csv",
            file=str(csv_path),
            confirmed=True,
            header=False,
        )
        process_subscriber_csv(csv.pk)
        subs = Subscriber.objects.filter(from_csv=csv)
        assert subs.count() == 2
        assert all(s.source == Subscriber.Source.CSV_IMPORT for s in subs)


class TestAcquisitionSummary:
    def test_totals_grouped_by_source(self):
        start_ts, end_ts = _window()
        Subscriber.objects.create(email="d1@example.com", source=Subscriber.Source.DRAWER)
        Subscriber.objects.create(email="d2@example.com", source=Subscriber.Source.DRAWER)
        Subscriber.objects.create(email="f1@example.com", source=Subscriber.Source.SUBSCRIBE_FORM)
        csv = SubscriberCSV.objects.create(name="x.csv", file="x.csv")
        Subscriber.objects.create(email="c1@example.com", from_csv=csv, source=Subscriber.Source.CSV_IMPORT)

        summary = _acquisition_summary(start_ts, end_ts)
        by_source = {row["source"]: row["count"] for row in summary["by_source"]}
        assert by_source[Subscriber.Source.DRAWER] == 2
        assert by_source[Subscriber.Source.SUBSCRIBE_FORM] == 1
        assert by_source[Subscriber.Source.CSV_IMPORT] == 1
        assert summary["total"] == 4

    def test_unsubscribed_in_window_counted(self):
        start_ts, end_ts = _window()
        sub = Subscriber.objects.create(email="bye@example.com", source=Subscriber.Source.DRAWER)
        sub.subscribed = False
        sub.save(update_fields=["subscribed", "modified"])
        summary = _acquisition_summary(start_ts, end_ts)
        assert summary["unsubscribed"] == 1

    def test_out_of_window_excluded(self):
        start_ts, end_ts = _window()
        old = Subscriber.objects.create(email="old@example.com", source=Subscriber.Source.DRAWER)
        Subscriber.objects.filter(pk=old.pk).update(created=start_ts - datetime.timedelta(days=10))
        summary = _acquisition_summary(start_ts, end_ts)
        assert summary["total"] == 0


class TestRecentFeed:
    def test_csv_collapsed_to_single_entry(self):
        start_ts, end_ts = _window()
        csv = SubscriberCSV.objects.create(name="spring-2026.csv", file="x.csv", email_added_count=42, processed=True)
        for i in range(5):
            Subscriber.objects.create(email=f"csv-{i}@example.com", from_csv=csv, source=Subscriber.Source.CSV_IMPORT)
        Subscriber.objects.create(email="d@example.com", source=Subscriber.Source.DRAWER)

        feed = _recent_subscriber_feed(start_ts, end_ts, limit=20)
        kinds = [e["kind"] for e in feed]
        assert kinds.count("csv") == 1
        assert kinds.count("individual") == 1
        csv_row = next(e for e in feed if e["kind"] == "csv")
        assert csv_row["name"] == "spring-2026.csv"
        assert csv_row["count"] == 42

    def test_csv_entry_omitted_if_not_processed(self):
        start_ts, end_ts = _window()
        SubscriberCSV.objects.create(name="pending.csv", file="x.csv", processed=False)
        feed = _recent_subscriber_feed(start_ts, end_ts, limit=20)
        assert not any(e["kind"] == "csv" for e in feed)

    def test_feed_sorted_newest_first(self):
        start_ts, end_ts = _window()
        older = Subscriber.objects.create(email="older@example.com", source=Subscriber.Source.DRAWER)
        Subscriber.objects.filter(pk=older.pk).update(created=timezone.now() - datetime.timedelta(hours=5))
        Subscriber.objects.create(email="newer@example.com", source=Subscriber.Source.DRAWER)

        feed = _recent_subscriber_feed(start_ts, end_ts, limit=20)
        emails = [e["email"] for e in feed if e["kind"] == "individual"]
        assert emails[0] == "newer@example.com"
