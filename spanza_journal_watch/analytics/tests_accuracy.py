"""Recording-layer accuracy: newsletter opens and clicks, beacons, visitor IDs."""

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.links import EmailLinkBuilder
from spanza_journal_watch.analytics.middleware import VISITOR_COOKIE_NAME
from spanza_journal_watch.analytics.models import (
    SCANNER_BURST_MIN_URLS,
    AnalyticsEvent,
    AutomatedRequestCount,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.analytics.tasks import (
    mark_newsletter_scanner_bursts_task,
    reclassify_newsletter_opens_task,
)
from spanza_journal_watch.analytics.utils import (
    REFERRER_NEWSLETTER,
    classify_automated_reason,
    classify_user_agent_reason,
)
from spanza_journal_watch.newsletter.models import Newsletter, NewsletterDelivery, Subscriber
from spanza_journal_watch.submissions.models import Issue

pytestmark = pytest.mark.django_db

SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
SCANNER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
GMAIL_PROXY_UA = "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)"


def _newsletter(send_date=None):
    issue = Issue.objects.create(name="Accuracy Issue", body="Issue body")
    return Newsletter.objects.create(
        issue=issue,
        subject="Accuracy newsletter",
        send_date=send_date or timezone.now() - timedelta(hours=2),
    )


def _subscriber(email="reader@example.com"):
    subscriber = Subscriber.objects.create(email=email, subscribed=True)
    subscriber.ensure_tracking_token()
    subscriber.save()
    return subscriber


def _link(subscriber, newsletter, destination):
    """The request path of the tracked newsletter link to ``destination``."""
    return EmailLinkBuilder(subscriber, newsletter_token=newsletter.email_token, domain="").url(destination)


def _pixel(subscriber, newsletter):
    return EmailLinkBuilder(subscriber, newsletter_token=newsletter.email_token, domain="").pixel_url()


def _backdate(model, queryset, delta):
    for pk, timestamp in queryset.values_list("pk", "timestamp"):
        model.objects.filter(pk=pk).update(timestamp=timestamp - delta)


# ---- Open classification ----


@pytest.mark.parametrize(
    ("user_agent", "headers", "automated", "reason"),
    [
        ("Mozilla/5.0", {}, True, "apple_mpp"),
        (GMAIL_PROXY_UA, {}, False, ""),
        (SAFARI_UA, {"HTTP_SEC_FETCH_MODE": "no-cors", "HTTP_SEC_FETCH_SITE": "cross-site"}, False, ""),
        ("Googlebot/2.1", {}, True, "ua_marker"),
        ("", {}, True, "empty_ua"),
        (SAFARI_UA, {"HTTP_PURPOSE": "prefetch"}, True, "prefetch"),
    ],
)
def test_open_pixel_classification(client, user_agent, headers, automated, reason):
    newsletter = _newsletter()
    subscriber = _subscriber()

    response = client.get(_pixel(subscriber, newsletter), HTTP_USER_AGENT=user_agent, **headers)

    assert response.status_code == 200
    open_ = NewsletterOpen.objects.get()
    assert open_.automated is automated
    assert open_.automated_reason == reason


def test_bare_mozilla_is_apple_mpp_after_stripping_and_lowercasing():
    assert classify_user_agent_reason("  MOZILLA/5.0 ", open_pixel=True) == "apple_mpp"
    assert classify_user_agent_reason("Mozilla/5.0", open_pixel=False) is None


def test_site_traffic_keeps_image_proxy_rules(rf):
    assert classify_automated_reason(rf.get("/", HTTP_USER_AGENT=GMAIL_PROXY_UA)) == "ua_marker"
    request = rf.get("/", HTTP_USER_AGENT=SAFARI_UA, HTTP_SEC_FETCH_MODE="no-cors", HTTP_SEC_FETCH_SITE="cross-site")
    assert classify_automated_reason(request) == "image_proxy"


def test_open_pixel_does_not_write_session(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    client.get(_pixel(subscriber, newsletter), HTTP_USER_AGENT=SAFARI_UA)

    assert "subscriber_id" not in client.session


def test_open_pixel_head_records_nothing(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    response = client.head(_pixel(subscriber, newsletter), HTTP_USER_AGENT=SAFARI_UA)

    assert response.status_code == 200
    assert not NewsletterOpen.objects.exists()


# ---- Per-delivery send window ----


def test_send_window_is_measured_from_the_subscribers_delivery(client):
    newsletter = _newsletter(send_date=timezone.now() - timedelta(minutes=20))
    just_delivered = _subscriber("late@example.com")
    delivered_early = _subscriber("early@example.com")
    NewsletterDelivery.objects.create(newsletter=newsletter, subscriber=just_delivered)
    early = NewsletterDelivery.objects.create(newsletter=newsletter, subscriber=delivered_early)
    NewsletterDelivery.objects.filter(pk=early.pk).update(sent_at=timezone.now() - timedelta(minutes=19))

    client.get(_pixel(just_delivered, newsletter), HTTP_USER_AGENT=SAFARI_UA)
    client.get(_pixel(delivered_early, newsletter), HTTP_USER_AGENT=SAFARI_UA)

    assert NewsletterOpen.objects.get(subscriber=just_delivered).automated_reason == "send_window"
    assert NewsletterOpen.objects.get(subscriber=delivered_early).automated is False


def test_send_window_falls_back_to_send_date_without_a_delivery(client):
    newsletter = _newsletter(send_date=timezone.now() - timedelta(seconds=5))
    subscriber = _subscriber()

    client.get(_link(subscriber, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)

    click = NewsletterClick.objects.get()
    assert click.automated is True
    assert click.automated_reason == "send_window"


# ---- Click recording ----


def test_click_head_redirects_and_records_nothing(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    response = client.head(_link(subscriber, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)

    assert response.status_code == 302
    assert not NewsletterClick.objects.exists()
    assert not AnalyticsEvent.objects.exists()


def test_click_to_external_destination_records_click_but_no_page_visit(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    response = client.get(_link(subscriber, newsletter, "https://doi.org/10.1000/xyz"), HTTP_USER_AGENT=SAFARI_UA)

    assert response["Location"] == "https://doi.org/10.1000/xyz"
    assert NewsletterClick.objects.filter(destination_url="https://doi.org/10.1000/xyz").exists()
    assert not AnalyticsEvent.objects.exists()


def test_click_event_has_landing_page_and_click_link(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    client.get(_link(subscriber, newsletter, "http://testserver/reviews/some-review/?x=1"), HTTP_USER_AGENT=SAFARI_UA)

    click = NewsletterClick.objects.get()
    event = AnalyticsEvent.objects.get(source="newsletter_click")
    assert event.landing_page == "/reviews/some-review/"
    assert event.metadata["newsletter_click_id"] == click.pk
    assert event.referrer_category == REFERRER_NEWSLETTER
    assert event.automated is False


def test_automated_click_passes_its_verdict_to_the_event(client):
    newsletter = _newsletter(send_date=timezone.now())
    subscriber = _subscriber()

    client.get(_link(subscriber, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)

    event = AnalyticsEvent.objects.get(source="newsletter_click")
    assert event.automated is True
    assert AutomatedRequestCount.objects.get(reason="send_window").count == 1


def test_click_keeps_a_different_subscriber_already_in_the_session(client):
    newsletter = _newsletter()
    recipient = _subscriber("recipient@example.com")
    forwarded_to = _subscriber("colleague@example.com")
    session = client.session
    session["subscriber_id"] = forwarded_to.pk
    session.save()

    client.get(_link(recipient, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)

    assert client.session["subscriber_id"] == forwarded_to.pk


def test_click_is_recorded_without_identifying_the_session(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    response = client.get(_link(subscriber, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)

    assert NewsletterClick.objects.filter(subscriber=subscriber, newsletter=newsletter).exists()
    assert response.cookies["jw_sub"].value == "1"
    assert "subscriber_id" not in client.session
    assert "subscriber_email" not in client.session


def test_forwarded_link_does_not_show_the_recipient_address_in_the_drawer(client):
    newsletter = _newsletter()
    recipient = _subscriber("recipient@example.com")

    client.get(_link(recipient, newsletter, "/"), HTTP_USER_AGENT=SAFARI_UA)
    content = client.get("/").content.decode()

    assert "You're subscribed" in content
    assert "recipient@example.com" not in content
    assert "drawer-unsubscribe" not in content


def test_confirmation_email_click_is_not_a_newsletter_click(client):
    subscriber = _subscriber()
    url = EmailLinkBuilder(subscriber, domain="").url("/")

    client.get(url, HTTP_USER_AGENT=SAFARI_UA)

    event = AnalyticsEvent.objects.get()
    assert event.source == "email_click"
    assert event.referrer_category != REFERRER_NEWSLETTER
    assert event.landing_page == "/"


# ---- Scanner bursts at write time ----


def _burst(client, subscriber, newsletter, count=SCANNER_BURST_MIN_URLS + 1):
    for i in range(count):
        client.get(_link(subscriber, newsletter, f"/reviews/r{i}/"), HTTP_USER_AGENT=SCANNER_UA)


def test_scanner_burst_marks_every_click_and_event(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    _burst(client, subscriber, newsletter)

    clicks = NewsletterClick.objects.all()
    assert clicks.count() == SCANNER_BURST_MIN_URLS + 1
    assert set(clicks.values_list("automated_reason", flat=True)) == {"scanner_burst"}
    events = AnalyticsEvent.objects.filter(source="newsletter_click")
    assert events.count() == SCANNER_BURST_MIN_URLS + 1
    assert not events.filter(automated=False).exists()
    # Each event is counted once, whether marked as it was recorded or after.
    assert AutomatedRequestCount.objects.get(reason="scanner_burst").count == SCANNER_BURST_MIN_URLS + 1


def test_few_distinct_urls_are_not_a_burst(client):
    newsletter = _newsletter()
    subscriber = _subscriber()

    _burst(client, subscriber, newsletter, count=SCANNER_BURST_MIN_URLS - 1)
    client.get(_link(subscriber, newsletter, "/reviews/r0/"), HTTP_USER_AGENT=SCANNER_UA)

    assert not NewsletterClick.objects.filter(automated=True).exists()


def test_reader_who_was_also_scanned_keeps_their_own_clicks(client):
    newsletter = _newsletter()
    subscriber = _subscriber()
    _burst(client, subscriber, newsletter)
    _backdate(NewsletterClick, NewsletterClick.objects.all(), timedelta(hours=1))
    _backdate(AnalyticsEvent, AnalyticsEvent.objects.all(), timedelta(hours=1))

    reader = Client(headers={"user-agent": SAFARI_UA})
    reader.get(_link(subscriber, newsletter, "/reviews/r1/"), HTTP_USER_AGENT=SAFARI_UA)
    reader.get(_link(subscriber, newsletter, "/reviews/r2/"), HTTP_USER_AGENT=SAFARI_UA)

    human = NewsletterClick.objects.filter(automated=False)
    assert human.count() == 2
    assert AnalyticsEvent.objects.filter(source="newsletter_click", automated=False).count() == 2


# ---- Burst sweeper ----


def _click(subscriber, newsletter, url, at, *, with_event=True):
    click = NewsletterClick.objects.create(
        subscriber=subscriber, newsletter=newsletter, destination_url=url, user_agent=SCANNER_UA
    )
    NewsletterClick.objects.filter(pk=click.pk).update(timestamp=at)
    if with_event:
        # Shaped like an event recorded before the click ID was stored.
        event = AnalyticsEvent.objects.create(
            event_type=AnalyticsEvent.EventType.PAGE_VISIT,
            source="newsletter_click",
            subscriber=subscriber,
            metadata={"newsletter_id": newsletter.pk, "destination_url": url},
        )
        AnalyticsEvent.objects.filter(pk=event.pk).update(timestamp=at + timedelta(milliseconds=30))
    return click


def test_burst_sweeper_marks_historical_bursts_idempotently():
    newsletter = _newsletter()
    subscriber = _subscriber()
    start = timezone.now() - timedelta(days=3)
    for i in range(7):
        _click(subscriber, newsletter, f"https://example.org/r{i}", start + timedelta(seconds=i))
    genuine = _click(subscriber, newsletter, "https://example.org/r1", start + timedelta(hours=2))

    first = mark_newsletter_scanner_bursts_task(lookback_days=None)
    second = mark_newsletter_scanner_bursts_task(lookback_days=None)

    assert first["marked"] == 7
    assert second["marked"] == 0
    assert NewsletterClick.objects.filter(automated_reason="scanner_burst").count() == 7
    genuine.refresh_from_db()
    assert genuine.automated is False
    assert AnalyticsEvent.objects.filter(automated=True).count() == 7
    assert AnalyticsEvent.objects.filter(automated=False).count() == 1
    assert AutomatedRequestCount.objects.get(reason="scanner_burst").count == 7


def test_burst_sweeper_ignores_blank_destinations():
    newsletter = _newsletter()
    subscriber = _subscriber()
    start = timezone.now() - timedelta(days=1)
    for i in range(7):
        _click(subscriber, newsletter, "", start + timedelta(seconds=i), with_event=False)

    assert mark_newsletter_scanner_bursts_task(lookback_days=None)["burst_clicks"] == 0


# ---- Open sweeper ----


def test_open_sweeper_reclassifies_history_idempotently():
    newsletter = _newsletter(send_date=timezone.now() - timedelta(days=2))
    subscriber = _subscriber()

    def make(user_agent, automated, after_send):
        open_ = NewsletterOpen.objects.create(
            subscriber=subscriber, newsletter=newsletter, user_agent=user_agent, automated=automated
        )
        NewsletterOpen.objects.filter(pk=open_.pk).update(timestamp=newsletter.send_date + after_send)
        return open_.pk

    gmail = make(GMAIL_PROXY_UA, True, timedelta(hours=1))
    mpp = make("Mozilla/5.0", False, timedelta(hours=1))
    webmail = make(SAFARI_UA, True, timedelta(hours=1))
    prompt = make(SAFARI_UA, False, timedelta(seconds=10))
    # Opens from before user-agents were stored keep their recorded verdict.
    unrecorded = make("", False, timedelta(seconds=10))

    first = reclassify_newsletter_opens_task(lookback_days=None)
    second = reclassify_newsletter_opens_task(lookback_days=None)

    rows = {o.pk: o for o in NewsletterOpen.objects.all()}
    assert (rows[gmail].automated, rows[gmail].automated_reason) == (False, "")
    assert (rows[mpp].automated, rows[mpp].automated_reason) == (True, "apple_mpp")
    assert (rows[webmail].automated, rows[webmail].automated_reason) == (False, "")
    assert (rows[prompt].automated, rows[prompt].automated_reason) == (True, "send_window")
    assert (rows[unrecorded].automated, rows[unrecorded].automated_reason) == (False, "")
    assert first["human"] == 2
    assert second == {"automated": {}, "human": 0, "dry_run": False}


# ---- Beacons: staff and client visitor IDs ----


def _beacon(client, **payload):
    data = {
        "event_type": AnalyticsEvent.EventType.PAGE_VISIT,
        "source": "page_load",
        "metadata": {"path": "/"},
        "page_url": "http://testserver/",
        "referrer": "",
    }
    data.update(payload)
    return client.post(reverse("reader_action"), data=data, content_type="application/json")


def test_staff_beacons_are_not_recorded(client):
    staff = get_user_model().objects.create_user(email="staff@example.com", password="pw-123456", is_staff=True)
    client.force_login(staff)

    response = _beacon(client)

    assert response.status_code == 200
    assert not AnalyticsEvent.objects.exists()


def test_first_visit_beacons_share_the_client_visitor_id():
    visitor_id = str(uuid.uuid4())
    # Two beacons in flight before either response has set the cookie.
    first, second = Client(headers={"user-agent": SAFARI_UA}), Client(headers={"user-agent": SAFARI_UA})

    response = _beacon(first, visitor_id=visitor_id)
    _beacon(second, visitor_id=visitor_id, event_type=AnalyticsEvent.EventType.REVIEW_OPEN)

    assert {str(v) for v in AnalyticsEvent.objects.values_list("visitor_id", flat=True)} == {visitor_id}
    assert response.cookies[VISITOR_COOKIE_NAME].value == visitor_id


def test_cookie_visitor_id_wins_over_client_id(client):
    cookie_id = str(uuid.uuid4())
    client.cookies[VISITOR_COOKIE_NAME] = cookie_id

    response = _beacon(client, visitor_id=str(uuid.uuid4()))

    assert str(AnalyticsEvent.objects.get().visitor_id) == cookie_id
    assert VISITOR_COOKIE_NAME not in response.cookies


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-uuid",
        "{" + str(uuid.uuid4()) + "}",
        uuid.uuid4().hex,
        str(uuid.uuid1()),
        str(uuid.uuid4()) + "x",
        12345,
    ],
)
def test_malformed_client_visitor_id_is_not_adopted(client, bad_id):
    response = _beacon(client, visitor_id=bad_id)

    minted = response.cookies[VISITOR_COOKIE_NAME].value
    assert str(uuid.UUID(minted)) == minted
    assert minted != str(bad_id)
    assert str(AnalyticsEvent.objects.get().visitor_id) == minted
