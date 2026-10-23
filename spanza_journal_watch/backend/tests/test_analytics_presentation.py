"""Presentation-layer analytics: newsletter rates, segments, unsubscribes, send stats and panels."""

import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent, NewsletterClick, NewsletterOpen
from spanza_journal_watch.backend.analytics_views.email import _acquisition_summary, _subscriber_segments
from spanza_journal_watch.backend.analytics_views.rates import newsletter_rate
from spanza_journal_watch.newsletter.models import Newsletter, NewsletterDelivery, Subscriber
from spanza_journal_watch.submissions.models import Issue, Review

pytestmark = pytest.mark.django_db


def _newsletter(name="Rates", send_date=None, emails_sent=0):
    issue = Issue.objects.create(name=f"{name} issue", body="body")
    return Newsletter.objects.create(
        issue=issue,
        subject=name,
        is_sent=send_date is not None,
        send_date=send_date,
        emails_sent=emails_sent,
    )


def _sub(email, **kwargs):
    return Subscriber.objects.create(email=email, **kwargs)


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser(email="presentation@example.com", password="pw")
    client.force_login(user)
    return client


# ── Rates ────────────────────────────────────────────────────────────


class TestNewsletterRates:
    def test_opened_is_union_of_real_openers_and_clickers(self):
        nl = _newsletter(send_date=timezone.now(), emails_sent=10)
        opener, clicker_only, both, mpp = (_sub(f"r{i}@example.com") for i in range(4))
        NewsletterOpen.objects.create(newsletter=nl, subscriber=opener)
        NewsletterOpen.objects.create(newsletter=nl, subscriber=both)
        NewsletterClick.objects.create(newsletter=nl, subscriber=both)
        NewsletterClick.objects.create(newsletter=nl, subscriber=clicker_only)
        NewsletterClick.objects.create(newsletter=nl, subscriber=clicker_only)
        NewsletterOpen.objects.create(newsletter=nl, subscriber=mpp, automated=True, automated_reason="apple_mpp")

        rates = newsletter_rate(nl)

        assert rates.real_openers == 2
        assert rates.real_clickers == 2
        assert rates.opened == 3
        assert rates.machine_openers == 1
        assert rates.open_rate == "30%"
        assert rates.click_rate == "20%"
        assert rates.click_to_open == "67%"

    def test_click_to_open_never_exceeds_100_when_pixels_are_blocked(self):
        nl = _newsletter(send_date=timezone.now(), emails_sent=5)
        for i in range(3):
            NewsletterClick.objects.create(newsletter=nl, subscriber=_sub(f"blocked{i}@example.com"))

        rates = newsletter_rate(nl)

        assert rates.real_openers == 0
        assert rates.opened == 3
        assert rates.click_to_open == "100%"

    def test_scanner_clicks_and_testers_are_excluded(self):
        nl = _newsletter(send_date=timezone.now(), emails_sent=4)
        reader = _sub("reader@example.com")
        tester = _sub("tester@example.com", tester=True)
        NewsletterDelivery.objects.create(newsletter=nl, subscriber=reader)
        NewsletterDelivery.objects.create(newsletter=nl, subscriber=tester)
        NewsletterOpen.objects.create(newsletter=nl, subscriber=tester)
        NewsletterClick.objects.create(newsletter=nl, subscriber=tester)
        NewsletterClick.objects.create(
            newsletter=nl, subscriber=reader, automated=True, automated_reason="scanner_burst"
        )

        rates = newsletter_rate(nl)

        assert rates.emails_sent == 3
        assert rates.opened == 0
        assert rates.real_clickers == 0
        assert rates.click_events == 1
        assert rates.automated_click_share == "100%"

    def test_release_page_and_email_panel_report_the_same_rates(self, admin_client):
        nl = _newsletter(send_date=timezone.now() - datetime.timedelta(days=2), emails_sent=10)
        for i in range(2):
            NewsletterClick.objects.create(newsletter=nl, subscriber=_sub(f"same{i}@example.com"))

        panel = admin_client.get(reverse("backend:analytics_email")).context["newsletter_rows"][0]["rates"]
        detail = admin_client.get(reverse("backend:newsletter_stats_detail", kwargs={"pk": nl.pk})).context["rates"]

        assert panel.click_to_open == detail.click_to_open == "100%"
        assert panel.click_rate == detail.click_rate == "20%"


# ── Segments ─────────────────────────────────────────────────────────


class TestSubscriberSegments:
    def test_subscriber_is_scored_only_on_newsletters_received(self):
        now = timezone.now()
        old = _newsletter("Old", send_date=now - datetime.timedelta(days=60))
        new = _newsletter("New", send_date=now - datetime.timedelta(days=5))
        late_joiner = _sub("late@example.com")
        NewsletterDelivery.objects.create(newsletter=new, subscriber=late_joiner)
        # A ledger exists for ``old`` but this subscriber is not in it.
        NewsletterDelivery.objects.create(newsletter=old, subscriber=_sub("other@example.com"))
        NewsletterOpen.objects.create(newsletter=new, subscriber=late_joiner)
        NewsletterClick.objects.create(newsletter=new, subscriber=late_joiner)

        segments = _subscriber_segments([new, old])

        # 1 of 1 received: highly engaged, rather than 1 of 2 (occasional). ``other`` received
        # only ``old`` and engaged with none, so is dormant.
        assert segments == {"highly_engaged": 1, "occasional": 0, "dormant": 1}

    def test_sends_without_a_ledger_fall_back_to_join_date(self):
        now = timezone.now()
        pre_ledger = _newsletter("Pre-ledger", send_date=now - datetime.timedelta(days=30))
        newcomer = _sub("newcomer@example.com")
        Subscriber.objects.filter(pk=newcomer.pk).update(created=now - datetime.timedelta(days=1))

        segments = _subscriber_segments([pre_ledger])

        assert segments == {"highly_engaged": 0, "occasional": 0, "dormant": 0}

    def test_bounced_and_complained_subscribers_are_left_out(self):
        nl = _newsletter(send_date=timezone.now())
        for kwargs in ({"bounced": True}, {"complained": True}):
            sub = _sub(f"{next(iter(kwargs))}@example.com", **kwargs)
            NewsletterDelivery.objects.create(newsletter=nl, subscriber=sub)

        assert _subscriber_segments([nl]) == {"highly_engaged": 0, "occasional": 0, "dormant": 0}


# ── Unsubscribes ─────────────────────────────────────────────────────


class TestUnsubscribedAt:
    def test_save_with_update_fields_stamps_and_clears(self):
        sub = _sub("toggle@example.com")
        assert sub.unsubscribed_at is None

        sub.subscribed = False
        sub.save(update_fields=["subscribed", "modified"])
        sub.refresh_from_db()
        first = sub.unsubscribed_at
        assert first is not None

        # A repeat unsubscribe keeps the first timestamp.
        sub.save(update_fields=["subscribed"])
        sub.refresh_from_db()
        assert sub.unsubscribed_at == first

        sub.subscribed = True
        sub.save(update_fields=["subscribed", "modified"])
        sub.refresh_from_db()
        assert sub.unsubscribed_at is None

    def test_unsubscribe_link_sets_it(self, client):
        sub = _sub("link@example.com")
        client.post(reverse("newsletter:unsubscribe", kwargs={"unsubscribe_token": sub.unsubscribe_token}))
        sub.refresh_from_db()
        assert sub.subscribed is False
        assert sub.unsubscribed_at is not None

    def test_bulk_sync_sets_and_clears_it(self):
        from spanza_journal_watch.newsletter.views import _sync_exact_email_subscription_state

        sub = _sub("bulk@example.com")
        _sync_exact_email_subscription_state("bulk@example.com", False)
        sub.refresh_from_db()
        assert sub.unsubscribed_at is not None
        _sync_exact_email_subscription_state("bulk@example.com", True)
        sub.refresh_from_db()
        assert sub.unsubscribed_at is None

    def test_window_counts_by_unsubscribe_time_not_last_edit(self):
        now = timezone.now()
        in_window = _sub("in@example.com", subscribed=False)
        old = _sub("old@example.com", subscribed=False)
        Subscriber.objects.filter(pk=old.pk).update(unsubscribed_at=now - datetime.timedelta(days=200), modified=now)
        _sub("bounce@example.com", subscribed=False, bounced=True)
        Subscriber.objects.filter(pk=in_window.pk).update(unsubscribed_at=now - datetime.timedelta(days=1))

        summary = _acquisition_summary(now - datetime.timedelta(days=30), now)

        assert summary["unsubscribed"] == 1


# ── Send stats email ─────────────────────────────────────────────────


def _ready(nl):
    nl.ready_to_send = True
    nl.is_test_sent = True
    nl.save()
    return nl


class TestSendStats:
    def test_stats_email_is_the_chord_callback(self):
        from spanza_journal_watch.newsletter.tasks import send_newsletter

        nl = _ready(_newsletter())
        _sub("chord@example.com")
        with (
            patch("spanza_journal_watch.newsletter.tasks.chord") as mock_chord,
            patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats") as mock_stats,
            patch("spanza_journal_watch.newsletter.tasks.send_newsletter_batch") as mock_batch,
        ):
            send_newsletter(nl.pk)

        mock_stats.delay.assert_not_called()
        mock_chord.assert_called_once_with([mock_batch.si.return_value])
        callback = mock_stats.si.return_value
        mock_chord.return_value.assert_called_once_with(callback)
        callback.link_error.assert_called_once()

    def test_resend_keeps_first_send_date(self):
        from spanza_journal_watch.newsletter.tasks import send_newsletter

        first = timezone.now() - datetime.timedelta(days=3)
        nl = _ready(_newsletter(send_date=first))
        Newsletter.objects.filter(pk=nl.pk).update(resend_enabled=True)
        with patch("spanza_journal_watch.newsletter.tasks.send_newsletter_stats"):
            send_newsletter(nl.pk)
        nl.refresh_from_db()
        assert nl.send_date == first

    def test_stats_report_this_runs_deliveries(self):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_stats

        nl = _newsletter(send_date=timezone.now(), emails_sent=3)
        earlier = NewsletterDelivery.objects.create(newsletter=nl, subscriber=_sub("earlier@example.com"))
        NewsletterDelivery.objects.filter(pk=earlier.pk).update(sent_at=timezone.now() - datetime.timedelta(days=1))
        run_started = timezone.now()
        NewsletterDelivery.objects.create(newsletter=nl, subscriber=_sub("now1@example.com"))
        NewsletterDelivery.objects.create(newsletter=nl, subscriber=_sub("now2@example.com"))

        send_newsletter_stats(
            nl.pk, 3, 1, "sender@example.com", already_delivered=1, run_started_at=run_started.isoformat()
        )

        body = mail.outbox[-1].body
        assert "Emails sent in this send: 2" in body
        assert "Journal Watch was not able to send 1 email." in body

    def test_failure_callback_still_sends_the_summary(self):
        from spanza_journal_watch.newsletter.tasks import send_newsletter_stats_after_failure

        nl = _newsletter(send_date=timezone.now())
        send_newsletter_stats_after_failure(None, RuntimeError("batch failed"), None, nl.pk, 0, 0, "s@example.com")
        assert len(mail.outbox) == 1


# ── Lift and panels ──────────────────────────────────────────────────


class TestEmailPanel:
    def test_lift_is_in_progress_until_the_week_has_passed(self, admin_client):
        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EventType.PAGE_VISIT, js_verified=True)
        AnalyticsEvent.objects.filter().update(timestamp=timezone.now() - datetime.timedelta(days=60))
        _newsletter("Recent", send_date=timezone.now() - datetime.timedelta(days=2), emails_sent=5)

        response = admin_client.get(reverse("backend:analytics_email"))

        (lift,) = response.context["newsletter_lift"]
        assert lift["in_progress"] is True
        assert lift["lift_pct"] is None
        assert response.context["newsletter_rows"][0]["post_send_in_progress"] is True
        assert "In progress" in response.content.decode()

    def test_active_subscribers_is_the_sendable_base(self, admin_client):
        _sub("ok@example.com")
        _sub("bounced@example.com", bounced=True)
        _sub("tester2@example.com", tester=True)
        _sub("gone@example.com", subscribed=False)

        response = admin_client.get(reverse("backend:analytics_email"))

        assert response.context["total_subscribers"] == 1


class TestEditorialPanel:
    def test_search_gaps_include_zero_result_queries_outside_the_top_searches(self, admin_client):
        for i in range(21):
            for _ in range(3):
                AnalyticsEvent.objects.create(
                    event_type=AnalyticsEvent.EventType.SEARCH, metadata={"query": f"popular {i}", "result_count": 4}
                )
        AnalyticsEvent.objects.create(
            event_type=AnalyticsEvent.EventType.SEARCH, metadata={"query": "rare gap", "result_count": 0}
        )

        response = admin_client.get(reverse("backend:analytics_editorial"))

        assert [row["label"] for row in response.context["zero_result_queries"]] == ["rare gap"]

    def test_search_click_summary_keeps_browse_apart(self):
        from spanza_journal_watch.backend.analytics_views.editorial import search_click_summary

        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EventType.SEARCH, metadata={"query": "ketamine"})
        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EventType.SEARCH, metadata={"query": ""})
        AnalyticsEvent.objects.create(
            event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK, metadata={"query": "ketamine"}
        )
        AnalyticsEvent.objects.create(event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK, metadata={"query": ""})

        summary = search_click_summary(AnalyticsEvent.objects.all())

        assert (summary["total_searches"], summary["total_clicks"], summary["search_ctr"]) == (1, 1, "100%")
        assert (summary["browse_searches"], summary["browse_ctr"]) == (1, "100%")


# ── Dead code ────────────────────────────────────────────────────────


class TestDeadCodeRemoved:
    def test_legacy_analytics_url_redirects_to_overview(self, admin_client):
        response = admin_client.get(reverse("backend:site_analytics"))
        assert response.status_code == 302
        assert response.url == reverse("backend:analytics_overview")

    @pytest.mark.parametrize("name", ["backend/analytics/search.html", "backend/analytics/_search_panel.html"])
    def test_unused_search_templates_are_gone(self, name):
        with pytest.raises(TemplateDoesNotExist):
            get_template(name)

    def test_review_get_hits_is_gone(self):
        assert not hasattr(Review, "get_hits")
