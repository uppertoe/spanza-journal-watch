import datetime
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import (
    AnalyticsEvent,
    AutomatedRequestCount,
    NewsletterClick,
    NewsletterOpen,
)
from spanza_journal_watch.analytics.utils import (
    _REFERRER_SESSION_KEY,
    NEWSLETTER_AUTOMATION_WINDOW,
    REFERRER_DIRECT,
    REFERRER_INTERNAL,
    REFERRER_NEWSLETTER,
    REFERRER_OTHER,
    REFERRER_SEARCH,
    REFERRER_SOCIAL,
    _categorize_from_header,
    categorize_referrer,
    classify_event_confidence,
    is_probable_automated_event,
    is_probable_automated_newsletter_event,
    set_newsletter_referrer_in_session,
)
from spanza_journal_watch.backend.models import PubmedArticle
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.submissions.models import Author, Issue, Journal, Review, Tag

pytestmark = pytest.mark.django_db


def _make_newsletter(send_date):
    issue = Issue.objects.create(name="Analytics Issue", body="Issue body")
    return Newsletter.objects.create(
        issue=issue,
        subject="Analytics newsletter",
        send_date=send_date,
    )


def test_newsletter_event_immediately_after_send_is_probably_automated():
    newsletter = _make_newsletter(send_date=timezone.now())
    request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")

    assert is_probable_automated_newsletter_event(request, newsletter) is True


def _make_review():
    journal = Journal.objects.create(name="Analytics Journal")
    author = Author.objects.create(name="Analytics Author")
    article = PubmedArticle.objects.create(
        title="Analytics Review Article",
        journal=journal,
        citation="Citation text",
        article_url="https://example.com/full-text",
        active=True,
    )
    review = Review(
        article=article,
        author=author,
        slug="analytics-review",
        body="### Summary\n\nThis is an analytics test review body.",
        active=True,
        publish_date=timezone.now(),
    )
    Review.objects.bulk_create([review])
    review = Review.objects.get(slug="analytics-review")
    issue = Issue.objects.create(name="Analytics Issue", body="Issue body", active=True)
    issue.reviews.add(review)
    return review


def test_track_event_records_review_share_event(client):
    review = _make_review()

    response = client.post(
        reverse("analytics:track_event"),
        data={
            "event_type": AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK,
            "review_id": review.pk,
            "source": "review_detail",
            "duration_ms": 12000,
            "scroll_depth": 64,
            "metadata": {"query": "airway"},
        },
        content_type="application/json",
        HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    )

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get()
    assert event.event_type == AnalyticsEvent.EventType.REVIEW_SHARE_COPY_LINK
    assert event.content_object == review
    assert event.source == "review_detail"
    assert event.duration_ms == 12000
    assert event.scroll_depth == 64
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_track_event_with_subscriber_session_records_known_subscriber_confidence(client):
    review = _make_review()
    subscriber = Subscriber.objects.create(email="reader@example.com", subscribed=True)
    session = client.session
    session["subscriber_id"] = subscriber.pk
    session.save()

    response = client.post(
        reverse("analytics:track_event"),
        data={
            "event_type": AnalyticsEvent.EventType.REVIEW_OPEN,
            "review_id": review.pk,
            "source": "review_detail",
        },
        content_type="application/json",
        HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    )

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)
    assert event.subscriber == subscriber
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.KNOWN_SUBSCRIBER_HUMAN


def test_search_page_records_search_analytics_event(client):
    if connection.vendor != "postgresql":
        pytest.skip("Search analytics route test requires PostgreSQL full-text search support.")

    _make_review()

    response = client.get(reverse("submissions:search"), {"q": "analytics"})

    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.SEARCH)
    assert event.metadata["query"] == "analytics"
    assert event.source == "search_page"


def test_site_analytics_redirects_to_overview(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        email="analytics@example.com",
        password="password123",
    )
    client.force_login(user)

    response = client.get(reverse("backend:site_analytics"))

    assert response.status_code == 302
    assert "/analytics/overview/" in response["Location"]


def test_newsletter_event_long_after_send_is_not_automated_without_markers():
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW - timedelta(seconds=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")

    assert is_probable_automated_newsletter_event(request, newsletter) is False


def test_newsletter_event_with_scanner_user_agent_is_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - timedelta(hours=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="GoogleImageProxy")

    assert is_probable_automated_newsletter_event(request, newsletter) is True


def test_classify_event_confidence_prioritises_automation_over_subscriber():
    subscriber = Subscriber.objects.create(email="subscriber@example.com", subscribed=True)

    assert (
        classify_event_confidence(automated=True, subscriber=subscriber)
        == AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED
    )


# ---- Visitor ID Middleware ----


def test_visitor_id_cookie_set_on_first_visit(client):
    response = client.get("/")
    assert "jwvid" in response.cookies
    cookie = response.cookies["jwvid"]
    assert cookie["httponly"]
    assert cookie["samesite"].lower() == "lax"


def test_visitor_id_cookie_not_reset_on_second_visit(client):
    r1 = client.get("/")
    first_value = r1.cookies["jwvid"].value
    r2 = client.get("/")
    # On second visit the browser sends the cookie, so server should not set a new one
    if "jwvid" in r2.cookies:
        assert r2.cookies["jwvid"].value == first_value


def test_stale_session_cookie_does_not_trigger_new_session_cookie_on_home(client):
    client.cookies["sessionid"] = "stale-session-cookie"

    response = client.get("/")

    assert response.status_code == 200
    assert response.cookies["sessionid"].value == ""


def test_stale_session_cookie_does_not_trigger_new_session_cookie_on_pageview(client):
    review = _make_review()
    client.cookies["sessionid"] = "stale-session-cookie"

    # Use a crawler UA so the pageview code path skips session writes — this
    # isolates the regression check (Django #11506) from legitimate session
    # updates that happen on real browser visits.
    response = client.get(
        reverse("analytics:page_view", args=["review", review.slug]),
        HTTP_USER_AGENT="Googlebot/2.1",
    )

    assert response.status_code == 200
    assert response.cookies["sessionid"].value == ""


# ---- Referrer categorisation ----


def test_empty_referer_is_direct():
    assert _categorize_from_header("") == REFERRER_DIRECT


def test_none_referer_is_direct():
    assert _categorize_from_header(None) == REFERRER_DIRECT


def test_google_referer_is_search():
    assert _categorize_from_header("https://www.google.com/search?q=anaesthesia") == REFERRER_SEARCH


def test_google_au_referer_is_search():
    assert _categorize_from_header("https://www.google.com.au/search?q=test") == REFERRER_SEARCH


def test_bing_referer_is_search():
    assert _categorize_from_header("https://www.bing.com/search?q=paediatric") == REFERRER_SEARCH


def test_x_com_referer_is_social():
    assert _categorize_from_header("https://x.com/some/link") == REFERRER_SOCIAL


def test_bluesky_referer_is_social():
    assert _categorize_from_header("https://bsky.app/profile/user") == REFERRER_SOCIAL


def test_facebook_referer_is_social():
    assert _categorize_from_header("https://www.facebook.com/") == REFERRER_SOCIAL


def test_unknown_external_is_other():
    assert _categorize_from_header("https://hospital.example.org/links") == REFERRER_OTHER


def test_own_domain_is_internal():
    assert _categorize_from_header("https://example.com/reviews/slug", own_domain="example.com") == REFERRER_INTERNAL


def test_newsletter_session_wins_over_google_referer(rf):
    request = rf.get("/", HTTP_REFERER="https://www.google.com/search?q=test")
    tomorrow = timezone.localtime().date() + datetime.timedelta(days=1)
    expires = timezone.make_aware(
        datetime.datetime.combine(tomorrow, datetime.time.min),
        timezone.get_current_timezone(),
    )
    request.session = {_REFERRER_SESSION_KEY: {"category": REFERRER_NEWSLETTER, "expires": expires.isoformat()}}
    assert categorize_referrer(request) == REFERRER_NEWSLETTER


def test_expired_newsletter_session_falls_back_to_header(rf):
    request = rf.get("/", HTTP_REFERER="https://www.google.com/search?q=test")
    yesterday = timezone.localtime() - datetime.timedelta(days=1)
    request.session = {_REFERRER_SESSION_KEY: {"category": REFERRER_NEWSLETTER, "expires": yesterday.isoformat()}}
    assert categorize_referrer(request) == REFERRER_SEARCH


def test_set_newsletter_referrer_expires_at_local_midnight(rf):
    request = rf.get("/")
    request.session = {}
    set_newsletter_referrer_in_session(request)
    entry = request.session.get(_REFERRER_SESSION_KEY)
    assert entry is not None
    assert entry["category"] == REFERRER_NEWSLETTER
    expires = datetime.datetime.fromisoformat(entry["expires"])
    # Should expire sometime after now but before 2 days from now
    assert timezone.now() < expires
    assert expires < timezone.now() + datetime.timedelta(days=2)


# ---- Bot filtering ----


def test_empty_user_agent_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="")
    assert is_probable_automated_event(request) is True


def test_semrushbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; SemrushBot/7~bl)")
    assert is_probable_automated_event(request) is True


def test_facebookexternalhit_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="facebookexternalhit/1.1")
    assert is_probable_automated_event(request) is True


def test_twitterbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Twitterbot/1.0")
    assert is_probable_automated_event(request) is True


def test_ahrefsbot_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; AhrefsBot/7.0)")
    assert is_probable_automated_event(request) is True


def test_normal_browser_not_automated(rf):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0"
    request = rf.get("/", HTTP_USER_AGENT=ua)
    assert is_probable_automated_event(request) is False


def test_barkrowler_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; Barkrowler/0.9; +https://babbar.tech/crawler)")
    assert is_probable_automated_event(request) is True


def test_sogou_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Sogou web spider/4.0(+http://www.sogou.com/docs/help/webmasters.htm#07)")
    assert is_probable_automated_event(request) is True


def test_cms_checker_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="CMS-Checker/1.0")
    assert is_probable_automated_event(request) is True


def test_generic_url_in_user_agent_is_automated(rf):
    # A UA we don't explicitly list but that advertises a URL is virtually
    # always a crawler (e.g., " +https://some.new.bot/info").
    request = rf.get(
        "/",
        HTTP_USER_AGENT="Mozilla/5.0 (compatible; UnknownBot/1.0; +https://unknownbot.example.com/about)",
    )
    assert is_probable_automated_event(request) is True


def test_search_event_without_sec_fetch_headers_is_automated(rf):
    # Real browsers always send sec-fetch-* on navigation requests. A SEARCH
    # event with none of them points to a scripted client.
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0"
    request = rf.get("/", HTTP_USER_AGENT=ua)
    assert is_probable_automated_event(request, event_type=AnalyticsEvent.EventType.SEARCH) is True


def test_search_event_with_sec_fetch_headers_is_not_automated(rf):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0"
    request = rf.get(
        "/",
        HTTP_USER_AGENT=ua,
        HTTP_SEC_FETCH_MODE="navigate",
        HTTP_SEC_FETCH_SITE="same-origin",
    )
    assert is_probable_automated_event(request, event_type=AnalyticsEvent.EventType.SEARCH) is False


def test_non_search_event_without_sec_fetch_headers_is_not_automated(rf):
    # Only SEARCH enforces the strict sec-fetch-* check; other event types
    # still pass when sec-fetch headers are absent.
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0"
    request = rf.get("/", HTTP_USER_AGENT=ua)
    assert is_probable_automated_event(request, event_type=AnalyticsEvent.EventType.PAGE_VISIT) is False


# ---- Newsletter automation window (60s) ----


def test_newsletter_event_at_exactly_60s_is_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - timedelta(seconds=59))
    request = RequestFactory().get(
        "/", HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    assert is_probable_automated_newsletter_event(request, newsletter) is True


def test_newsletter_event_after_60s_is_not_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW - timedelta(seconds=1))
    request = RequestFactory().get(
        "/", HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    assert is_probable_automated_newsletter_event(request, newsletter) is False


# ---- New event types recorded ----


def test_journal_browser_visit_event_accepted(client):
    _make_review()
    response = client.post(
        reverse("analytics:track_event"),
        data={"event_type": AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT).exists()


def test_journal_article_interact_event_accepted(client):
    _make_review()
    response = client.post(
        reverse("analytics:track_event"),
        data={"event_type": AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT, "metadata": {"action": "star"}},
        content_type="application/json",
    )
    assert response.status_code == 200
    event = AnalyticsEvent.objects.get(event_type=AnalyticsEvent.EventType.JOURNAL_ARTICLE_INTERACT)
    assert event.metadata["action"] == "star"


# ---- Analytics pages auth ----


def test_analytics_overview_requires_view_site_analytics(client):
    User = get_user_model()
    user = User.objects.create_user(email="noanalytics@example.com", password="pw")
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(reverse("backend:analytics_overview"))
    assert response.status_code == 403


def test_analytics_overview_accessible_with_view_site_analytics(client):
    User = get_user_model()
    user = User.objects.create_superuser(email="analytics2@example.com", password="pw")
    client.force_login(user)
    response = client.get(reverse("backend:analytics_overview"))
    assert response.status_code == 200


def test_analytics_email_requires_view_newsletter_stats(client):
    User = get_user_model()
    user = User.objects.create_user(email="noemail@example.com", password="pw")
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(reverse("backend:analytics_email"))
    assert response.status_code == 403


def test_analytics_overview_all_traffic_label_is_truthful(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="overview-all@example.com", password="password123")
    client.force_login(user)

    response = client.get(reverse("backend:analytics_overview"), {"filter": "all"})

    assert response.status_code == 200
    body = response.content.decode("utf-8", errors="ignore")
    assert "All traffic" in body
    assert "Human-filtered" not in body


def test_analytics_overview_surfaces_action_summaries_and_sidebar_guidance(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="overview-summary@example.com", password="password123")
    client.force_login(user)

    review = _make_review()
    for event_type in [
        AnalyticsEvent.EventType.REVIEW_OPEN,
        AnalyticsEvent.EventType.REVIEW_ENGAGED,
        AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
    ]:
        AnalyticsEvent.objects.create(
            event_type=event_type,
            automated=False,
            content_object=review,
        )

    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=uuid.uuid4(),
        session_key="overview-search",
        landing_page="/search",
        metadata={"page": "search"},
    )
    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH,
        automated=False,
        visitor_id=uuid.uuid4(),
        session_key="overview-search-dead-end",
        metadata={"query": "failed search", "result_count": 0},
    )

    response = client.get(reverse("backend:analytics_overview"))

    assert response.status_code == 200
    assert response.context["overview_editorial_items"]
    assert response.context["overview_dev_items"]
    assert response.context["overview_confidence_items"]
    body = response.content.decode("utf-8", errors="ignore")
    assert "Editors should know" in body
    assert "Developers should know" in body
    assert "Confidence & scope" in body
    assert "90d is usually the most stable default" in body
    assert "30d" in body
    assert "90d" in body
    assert "180d" in body


def test_analytics_email_uses_event_counts_for_bot_share_and_marks_partial_site_analytics(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="newsletter-analytics@example.com", password="password123")
    client.force_login(user)

    rollout_event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        js_verified=True,
    )
    rollout_ts = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0))
    AnalyticsEvent.objects.filter(pk=rollout_event.pk).update(timestamp=rollout_ts)

    newsletter = _make_newsletter(send_date=timezone.make_aware(datetime.datetime(2026, 1, 23, 9, 0)))
    newsletter.is_sent = True
    newsletter.emails_sent = 10
    newsletter.save(update_fields=["is_sent", "emails_sent", "send_date"])

    subscribers = [
        Subscriber.objects.create(email="one@example.com", subscribed=True),
        Subscriber.objects.create(email="two@example.com", subscribed=True),
        Subscriber.objects.create(email="three@example.com", subscribed=True),
    ]

    NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[0], automated=False)
    NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[0], automated=False)
    NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[1], automated=False)
    NewsletterOpen.objects.create(newsletter=newsletter, subscriber=subscribers[2], automated=True)

    NewsletterClick.objects.create(newsletter=newsletter, subscriber=subscribers[0], automated=False)

    response = client.get(reverse("backend:analytics_email"))

    assert response.status_code == 200
    row = response.context["newsletter_rows"][0]
    assert row["automated_open_share"] == "25%"
    assert row["human_ctr"] == "10%"
    assert row["human_ctor"] == "50%"
    assert row["site_analytics_partial"] is True
    assert row["post_send_traffic"] is None
    body = response.content.decode("utf-8", errors="ignore")
    assert "Partial analytics" in body


def test_analytics_editorial_excludes_placeholder_schema_search_queries(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="editorial-search@example.com", password="password123")
    client.force_login(user)

    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH,
        automated=False,
        metadata={"query": "{search_term_string}", "result_count": 0},
    )
    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH,
        automated=False,
        metadata={"query": "airway", "result_count": 0},
    )

    response = client.get(reverse("backend:analytics_editorial"))

    assert response.status_code == 200
    labels = [item["label"] for item in response.context["search_insights"]]
    assert "airway" in labels
    assert "{search_term_string}" not in labels


def test_analytics_editorial_surfaces_unmet_demand_and_review_rankings(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="editorial-decisions@example.com", password="password123")
    client.force_login(user)

    review = _make_review()
    tag = Tag.objects.create(text="Airway", curated=True)
    review.article.tags.add(tag)

    for event_type in [
        AnalyticsEvent.EventType.REVIEW_OPEN,
        AnalyticsEvent.EventType.REVIEW_ENGAGED,
        AnalyticsEvent.EventType.REVIEW_FULL_TEXT_CLICK,
        AnalyticsEvent.EventType.REVIEW_SHARE_EMAIL,
    ]:
        AnalyticsEvent.objects.create(
            event_type=event_type,
            automated=False,
            content_object=review,
        )

    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH,
        automated=False,
        metadata={"query": "airway", "result_count": 0},
    )

    response = client.get(reverse("backend:analytics_editorial"))

    assert response.status_code == 200
    assert response.context["unmet_demand"][0]["query"] == "airway"
    assert response.context["top_reviews_by_reach"][0]["review"] == review
    assert response.context["top_reviews_by_shares"][0]["review"] == review
    body = response.content.decode("utf-8", errors="ignore")
    assert "Unmet demand" in body
    assert "Most opened" in body
    assert "Most shared" in body


def test_analytics_traffic_excludes_subresource_landing_pages(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic@example.com", password="password123")
    client.force_login(user)

    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=uuid.uuid4(),
        session_key="session-one",
        session_sequence=1,
        landing_page="/sw.js",
        metadata={"page": "home"},
    )
    AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=uuid.uuid4(),
        session_key="session-two",
        session_sequence=1,
        landing_page="/search",
        metadata={"page": "search"},
    )

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    landing_paths = [item["path"] for item in response.context["landing_breakdown"]]
    assert "/search" in landing_paths
    assert "/sw.js" not in landing_paths


def test_analytics_traffic_splits_visits_on_inactivity_gap(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic-gap@example.com", password="password123")
    client.force_login(user)

    visitor_id = uuid.uuid4()
    first = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="shared-session",
        metadata={"page": "home"},
    )
    second = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="shared-session",
        metadata={"page": "search"},
    )
    AnalyticsEvent.objects.filter(pk=first.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 10, 9, 0))
    )
    AnalyticsEvent.objects.filter(pk=second.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 10, 10, 5))
    )

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    assert len(response.context["recent_sessions"]) == 2
    body = response.content.decode("utf-8", errors="ignore")
    assert "Recent visits" in body
    assert "30 minutes of inactivity" in body


def test_analytics_traffic_source_breakdown_uses_visit_starts_not_internal_follow_on_events(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic-sources@example.com", password="password123")
    client.force_login(user)

    visitor_id = uuid.uuid4()
    t0 = timezone.make_aware(datetime.datetime(2026, 4, 11, 9, 0))
    search_start = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="visit-source",
        referrer_category="search",
        metadata={"page": "home"},
        js_verified=True,
    )
    internal_follow_up = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
        automated=False,
        visitor_id=visitor_id,
        session_key="visit-source",
        referrer_category="internal",
        js_verified=True,
    )
    internal_engaged = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
        automated=False,
        visitor_id=visitor_id,
        session_key="visit-source",
        referrer_category="internal",
        js_verified=True,
    )
    AnalyticsEvent.objects.filter(pk=search_start.pk).update(timestamp=t0)
    AnalyticsEvent.objects.filter(pk=internal_follow_up.pk).update(timestamp=t0 + datetime.timedelta(minutes=2))
    AnalyticsEvent.objects.filter(pk=internal_engaged.pk).update(timestamp=t0 + datetime.timedelta(minutes=4))

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    breakdown = {item["label"]: item for item in response.context["referrer_breakdown"]}
    assert breakdown["Search engine"]["visits"] == 1
    assert breakdown["Search engine"]["visitors"] == 1
    assert breakdown["Search engine"]["engaged_rate"] == "100%"
    assert "Internal" not in breakdown
    body = response.content.decode("utf-8", errors="ignore")
    assert "Where visits start" in body
    assert "Visit starts:" in body


def test_analytics_traffic_campaign_breakdown_uses_first_touch_visits(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic-campaigns@example.com", password="password123")
    client.force_login(user)

    visitor_id = uuid.uuid4()
    started = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="utm-visit",
        metadata={
            "page": "home",
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "launch",
        },
    )
    follow_on = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
        automated=False,
        visitor_id=visitor_id,
        session_key="utm-visit",
        metadata={
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "launch",
        },
    )
    AnalyticsEvent.objects.filter(pk=started.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 12, 9, 0))
    )
    AnalyticsEvent.objects.filter(pk=follow_on.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 12, 9, 3))
    )

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    assert response.context["campaign_breakdown"] == [{"label": "newsletter / email / launch", "visits": 1}]
    body = response.content.decode("utf-8", errors="ignore")
    assert "First-touch visits tagged with UTM parameters" in body


def test_analytics_traffic_surfaces_landing_friction_and_search_dead_ends(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic-friction@example.com", password="password123")
    client.force_login(user)

    stuck_visitor = uuid.uuid4()
    successful_visitor = uuid.uuid4()

    stuck_visit = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=stuck_visitor,
        session_key="search-landing-stuck",
        landing_page="/search",
        metadata={"page": "search"},
    )
    successful_landing = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=successful_visitor,
        session_key="search-landing-success",
        landing_page="/search",
        metadata={"page": "search"},
    )
    successful_engaged = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.REVIEW_ENGAGED,
        automated=False,
        visitor_id=successful_visitor,
        session_key="search-landing-success",
    )
    dead_end_search = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH,
        automated=False,
        visitor_id=stuck_visitor,
        session_key="search-landing-stuck",
        metadata={"query": "failed search", "result_count": 0},
    )

    AnalyticsEvent.objects.filter(pk=stuck_visit.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 16, 9, 0))
    )
    AnalyticsEvent.objects.filter(pk=successful_landing.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 16, 10, 0))
    )
    AnalyticsEvent.objects.filter(pk=successful_engaged.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 16, 10, 3))
    )
    AnalyticsEvent.objects.filter(pk=dead_end_search.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 16, 9, 2))
    )

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    landing = next(item for item in response.context["landing_breakdown"] if item["path"] == "/search")
    assert landing["visits"] == 2
    assert landing["engaged_rate"] == "50%"
    assert landing["one_step_rate"] == "50%"
    assert response.context["search_dead_ends"][0]["query"] == "failed search"
    body = response.content.decode("utf-8", errors="ignore")
    assert "Search dead ends" in body
    assert "1-step rate" in body


def test_analytics_traffic_page_breakdown_uses_sections_touched_per_visit(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="traffic-sections@example.com", password="password123")
    client.force_login(user)

    visitor_id = uuid.uuid4()
    home = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="page-sections",
        metadata={"page": "home"},
    )
    search = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="page-sections",
        metadata={"page": "search"},
    )
    search_click = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.SEARCH_RESULT_CLICK,
        automated=False,
        visitor_id=visitor_id,
        session_key="page-sections",
        metadata={"query": "airway"},
    )
    AnalyticsEvent.objects.filter(pk=home.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 12, 10, 0))
    )
    AnalyticsEvent.objects.filter(pk=search.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 12, 10, 3))
    )
    AnalyticsEvent.objects.filter(pk=search_click.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 12, 10, 4))
    )

    response = client.get(reverse("backend:analytics_traffic"))

    assert response.status_code == 200
    page_breakdown = {item["label"]: item["visits"] for item in response.context["page_breakdown"]}
    assert page_breakdown["Homepage"] == 1
    assert page_breakdown["Search"] == 1
    body = response.content.decode("utf-8", errors="ignore")
    assert "Sections touched" in body
    assert "included each section at least once" in body


def test_analytics_overview_uses_derived_visits_for_visit_count(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="overview-visits@example.com", password="password123")
    client.force_login(user)

    visitor_id = uuid.uuid4()
    first = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="sticky-session",
        metadata={"page": "home"},
    )
    second = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor_id,
        session_key="sticky-session",
        metadata={"page": "search"},
    )
    AnalyticsEvent.objects.filter(pk=first.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 10, 9, 0))
    )
    AnalyticsEvent.objects.filter(pk=second.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 10, 10, 5))
    )

    response = client.get(reverse("backend:analytics_overview"))

    assert response.status_code == 200
    assert response.context["unique_sessions"] == 2
    body = response.content.decode("utf-8", errors="ignore")
    assert "Visits" in body


def test_analytics_journals_uses_derived_visits_and_prior_history_for_returning_visitors(client):
    user_model = get_user_model()
    user = user_model.objects.create_superuser(email="journals-visits@example.com", password="password123")
    client.force_login(user)

    returning_visitor = uuid.uuid4()
    new_visitor = uuid.uuid4()

    prior = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
        automated=False,
        visitor_id=returning_visitor,
        session_key="journal-prior",
    )
    first_current = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
        automated=False,
        visitor_id=returning_visitor,
        session_key="journal-current",
    )
    same_visit_follow_on = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.JOURNAL_SELECT,
        automated=False,
        visitor_id=returning_visitor,
        session_key="journal-current",
        metadata={"journal_id": 1, "journal_name": "Test Journal"},
    )
    second_visit = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.JOURNAL_BROWSER_VISIT,
        automated=False,
        visitor_id=new_visitor,
        session_key="journal-new",
    )

    AnalyticsEvent.objects.filter(pk=prior.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 1, 1, 10, 0))
    )
    AnalyticsEvent.objects.filter(pk=first_current.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 13, 9, 0))
    )
    AnalyticsEvent.objects.filter(pk=same_visit_follow_on.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 13, 9, 5))
    )
    AnalyticsEvent.objects.filter(pk=second_visit.pk).update(
        timestamp=timezone.make_aware(datetime.datetime(2026, 4, 13, 11, 0))
    )

    response = client.get(reverse("backend:analytics_journals"))

    assert response.status_code == 200
    assert response.context["total_visits"] == 2
    assert response.context["unique_visitors"] == 2
    assert response.context["returning_visitors"] == 1
    body = response.content.decode("utf-8", errors="ignore")
    assert "Journal visits are derived" in body
    assert "Journal visits" in body


# ---- extract_referrer_domain ----


def test_extract_referrer_domain_strips_www():
    from spanza_journal_watch.analytics.utils import extract_referrer_domain

    request = RequestFactory().get("/", HTTP_REFERER="https://www.google.com/search?q=test")
    assert extract_referrer_domain(request) == "google.com"


def test_extract_referrer_domain_empty_referer():
    from spanza_journal_watch.analytics.utils import extract_referrer_domain

    request = RequestFactory().get("/")
    assert extract_referrer_domain(request) == ""


def test_extract_referrer_domain_no_www():
    from spanza_journal_watch.analytics.utils import extract_referrer_domain

    request = RequestFactory().get("/", HTTP_REFERER="https://bsky.app/profile/user")
    assert extract_referrer_domain(request) == "bsky.app"


# ---- extract_utm_params ----


def test_extract_utm_params_returns_present_params():
    from spanza_journal_watch.analytics.utils import extract_utm_params

    request = RequestFactory().get("/?utm_source=newsletter&utm_medium=email&utm_campaign=jan2026")
    params = extract_utm_params(request)
    assert params == {"utm_source": "newsletter", "utm_medium": "email", "utm_campaign": "jan2026"}


def test_extract_utm_params_ignores_empty():
    from spanza_journal_watch.analytics.utils import extract_utm_params

    request = RequestFactory().get("/?utm_source=&utm_medium=email")
    params = extract_utm_params(request)
    assert params == {"utm_medium": "email"}
    assert "utm_source" not in params


def test_extract_utm_params_truncates_long_values():
    from spanza_journal_watch.analytics.utils import extract_utm_params

    request = RequestFactory().get(f"/?utm_source={'a' * 200}")
    params = extract_utm_params(request)
    assert len(params["utm_source"]) == 128


# ---- UTM-based referrer categorisation ----


def test_utm_newsletter_source_categorised():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/?utm_source=newsletter")
    assert _categorize_from_utm(request) == REFERRER_NEWSLETTER


def test_utm_email_source_categorised_as_newsletter():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/?utm_source=email")
    assert _categorize_from_utm(request) == REFERRER_NEWSLETTER


def test_utm_twitter_source_categorised_as_social():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/?utm_source=twitter")
    assert _categorize_from_utm(request) == REFERRER_SOCIAL


def test_utm_google_source_categorised_as_search():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/?utm_source=google")
    assert _categorize_from_utm(request) == REFERRER_SEARCH


def test_utm_unknown_source_categorised_as_other():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/?utm_source=partner_site")
    assert _categorize_from_utm(request) == REFERRER_OTHER


def test_utm_empty_returns_none():
    from spanza_journal_watch.analytics.utils import _categorize_from_utm

    request = RequestFactory().get("/")
    assert _categorize_from_utm(request) is None


# ---- Prefetch / sec-fetch header detection ----


def test_purpose_prefetch_header_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0", HTTP_PURPOSE="prefetch")
    assert is_probable_automated_event(request) is True


def test_x_purpose_preview_header_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_PURPOSE="preview")
    assert is_probable_automated_event(request) is True


def test_x_moz_prefetch_header_is_automated(rf):
    request = rf.get("/", HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_MOZ="prefetch")
    assert is_probable_automated_event(request) is True


def test_sec_fetch_no_cors_cross_site_is_automated(rf):
    request = rf.get(
        "/",
        HTTP_USER_AGENT="Mozilla/5.0",
        HTTP_SEC_FETCH_MODE="no-cors",
        HTTP_SEC_FETCH_SITE="cross-site",
    )
    assert is_probable_automated_event(request) is True


def test_sec_fetch_navigate_same_origin_not_automated(rf):
    request = rf.get(
        "/",
        HTTP_USER_AGENT="Mozilla/5.0",
        HTTP_SEC_FETCH_MODE="navigate",
        HTTP_SEC_FETCH_SITE="same-origin",
    )
    assert is_probable_automated_event(request) is False


# ---- Landing page capture in VisitorIdMiddleware ----


def test_landing_page_captured_in_session(client):
    client.get("/")
    session = client.session
    assert session.get("analytics_landing_page") == "/"


def test_share_token_captured_from_ref_param(client):
    client.get("/newsletter/success/?ref=abc123")
    session = client.session
    assert session.get("analytics_share_token") == "abc123"


# ---- SafeSessionCookieMiddleware ----


def test_manifest_response_has_no_session_cookie(client):
    """manifest.json is a sub-resource; its response must never carry sessionid."""
    client.cookies["sessionid"] = "stale-session-cookie"
    response = client.get("/manifest.json")
    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_sw_js_response_has_no_session_cookie(client):
    """sw.js is a sub-resource; its response must never carry sessionid."""
    client.cookies["sessionid"] = "stale-session-cookie"
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_robots_txt_response_has_no_session_cookie(client):
    client.cookies["sessionid"] = "stale-session-cookie"
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_favicon_response_has_no_session_cookie(client):
    client.cookies["sessionid"] = "stale-session-cookie"
    response = client.get("/favicon.ico")
    assert "sessionid" not in response.cookies


def test_track_event_with_stale_session_does_not_delete_cookie(client):
    """
    Regression test for Django #11506.  sendBeacon requests may carry a stale
    session cookie (deleted by cycle_key() during login).  The response must
    not include Set-Cookie — neither SET nor DELETE — because it would race
    with the authenticated session cookie from the login response.
    """
    client.cookies["sessionid"] = "stale-session-cookie"

    response = client.post(
        reverse("reader_action"),
        data={
            "event_type": AnalyticsEvent.EventType.PAGE_VISIT,
            "source": "scroll_depth",
            "scroll_depth": 42,
            "metadata": {"page": "home"},
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert "sessionid" not in response.cookies


def test_manifest_does_not_add_vary_cookie(client):
    """Sub-resource views should not trigger Vary: Cookie (breaks cacheability)."""
    response = client.get("/manifest.json")
    vary = response.get("Vary", "")
    assert "Cookie" not in vary


def test_visitor_id_still_set_on_sub_resource_paths(client):
    """VisitorIdMiddleware should still set the jwvid cookie on sub-resource paths."""
    response = client.get("/manifest.json")
    assert "jwvid" in response.cookies


# ---- downgrade_singleton_visitors_task ----


def _backdate(event, *, hours=24):
    """Move an event's timestamp into the past so it passes the min-age filter."""
    AnalyticsEvent.objects.filter(pk=event.pk).update(timestamp=timezone.now() - timedelta(hours=hours))


def test_downgrade_singleton_visitors_downgrades_non_js_single_event():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
        js_verified=False,
    )
    _backdate(event)

    result = downgrade_singleton_visitors_task()

    event.refresh_from_db()
    assert event.automated is True
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED
    assert result == {"downgraded": 1, "dry_run": False}


def test_downgrade_singleton_visitors_skips_js_verified_events():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
        js_verified=True,
    )
    _backdate(event)

    downgrade_singleton_visitors_task()

    event.refresh_from_db()
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_downgrade_singleton_visitors_skips_multi_event_visitors():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    first = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )
    second = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )
    _backdate(first)
    _backdate(second)

    downgrade_singleton_visitors_task()

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN
    assert second.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_downgrade_singleton_visitors_skips_recent_events():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )

    downgrade_singleton_visitors_task()

    event.refresh_from_db()
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_downgrade_singleton_visitors_skips_newsletter_referrer():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        referrer_category="newsletter",
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )
    _backdate(event)

    downgrade_singleton_visitors_task()

    event.refresh_from_db()
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN


def test_downgrade_singleton_visitors_is_idempotent():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )
    _backdate(event)

    downgrade_singleton_visitors_task()
    second_result = downgrade_singleton_visitors_task()

    event.refresh_from_db()
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.SUSPECTED_AUTOMATED
    assert second_result["downgraded"] == 0


def test_downgrade_singleton_visitors_dry_run_makes_no_changes():
    from spanza_journal_watch.analytics.tasks import downgrade_singleton_visitors_task

    visitor = uuid.uuid4()
    event = AnalyticsEvent.objects.create(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        automated=False,
        visitor_id=visitor,
        human_confidence=AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN,
    )
    _backdate(event)

    result = downgrade_singleton_visitors_task(dry_run=True)

    event.refresh_from_db()
    assert event.human_confidence == AnalyticsEvent.HumanConfidence.PROBABLE_HUMAN
    assert result == {"would_downgrade": 1, "downgraded": 0, "dry_run": True}


# ---- referrer_category default when request is None ----


def test_record_event_without_request_defaults_to_direct():
    event = AnalyticsEvent.record_event(event_type=AnalyticsEvent.EventType.PAGE_VISIT)
    assert event.referrer_category == REFERRER_DIRECT


# ---- Automated requests are dropped at record_event ----


def test_record_event_skips_automated_request(rf):
    request = rf.get("/", HTTP_USER_AGENT="Googlebot/2.1")
    result = AnalyticsEvent.record_event(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        request=request,
    )
    assert result is None
    assert AnalyticsEvent.objects.count() == 0


def test_record_event_bumps_counter_for_automated_request(rf):
    request = rf.get("/", HTTP_USER_AGENT="Googlebot/2.1")
    AnalyticsEvent.record_event(event_type=AnalyticsEvent.EventType.PAGE_VISIT, request=request)
    AnalyticsEvent.record_event(event_type=AnalyticsEvent.EventType.PAGE_VISIT, request=request)
    AnalyticsEvent.record_event(event_type=AnalyticsEvent.EventType.REVIEW_OPEN, request=request)

    page_visit_row = AutomatedRequestCount.objects.get(event_type=AnalyticsEvent.EventType.PAGE_VISIT)
    review_open_row = AutomatedRequestCount.objects.get(event_type=AnalyticsEvent.EventType.REVIEW_OPEN)
    assert page_visit_row.count == 2
    assert review_open_row.count == 1


def test_record_event_does_not_bump_counter_for_human_request(client):
    client.get("/")
    assert AutomatedRequestCount.objects.count() == 0


def test_page_visit_middleware_drops_bot_requests(client):
    client.get("/", HTTP_USER_AGENT="Googlebot/2.1")
    assert not AnalyticsEvent.objects.filter(source="server").exists()


# ---- Newsletter click redirectors emit AnalyticsEvent ----


def test_track_email_click_records_analytics_event(client):
    subscriber = Subscriber.objects.create(email="clicker@example.com", subscribed=True)
    response = client.get(
        reverse("analytics:track_email_click"),
        {"email": subscriber.email, "next": "/"},
    )
    assert response.status_code == 302
    events = AnalyticsEvent.objects.filter(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        source="newsletter_click",
    )
    assert events.count() == 1
    event = events.first()
    assert event.subscriber_id == subscriber.pk
    assert event.referrer_category == REFERRER_NEWSLETTER


def test_track_email_click_skips_event_when_no_subscriber(client):
    response = client.get(
        reverse("analytics:track_email_click"),
        {"email": "unknown@example.com", "next": "/"},
    )
    assert response.status_code == 302
    assert not AnalyticsEvent.objects.filter(source="newsletter_click").exists()


def test_track_newsletter_link_records_analytics_event(client):
    subscriber = Subscriber.objects.create(email="reader@example.com", subscribed=True)
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW * 2)
    response = client.get(
        reverse("analytics:track_newsletter_email_link", args=[newsletter.email_token]),
        {"email": subscriber.email, "next": "/"},
    )
    assert response.status_code == 302
    events = AnalyticsEvent.objects.filter(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        source="newsletter_click",
    )
    assert events.count() == 1
    event = events.first()
    assert event.subscriber_id == subscriber.pk
    assert event.referrer_category == REFERRER_NEWSLETTER
    assert event.metadata.get("newsletter_id") == newsletter.pk


# ---- PageVisitAnalyticsMiddleware ----


def test_page_visit_middleware_records_event_for_html_get(client):
    client.get("/")
    events = AnalyticsEvent.objects.filter(
        event_type=AnalyticsEvent.EventType.PAGE_VISIT,
        source="server",
    )
    assert events.count() == 1


def test_page_visit_middleware_captures_external_referer(client):
    client.get("/", HTTP_REFERER="https://www.google.com/search?q=foo")
    event = AnalyticsEvent.objects.filter(source="server").first()
    assert event is not None
    assert event.referrer_category == REFERRER_SEARCH


def test_page_visit_middleware_skips_htmx_requests(client):
    client.get("/", HTTP_HX_REQUEST="true")
    assert not AnalyticsEvent.objects.filter(source="server").exists()


def test_page_visit_middleware_skips_json_responses(client):
    _make_review()
    response = client.post(
        reverse("reader_action"),
        data={"event_type": AnalyticsEvent.EventType.PAGE_VISIT},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert not AnalyticsEvent.objects.filter(source="server").exists()


def test_page_visit_middleware_skips_redirects(client):
    subscriber = Subscriber.objects.create(email="redirect@example.com", subscribed=True)
    response = client.get(
        reverse("analytics:track_email_click"),
        {"email": subscriber.email, "next": "/"},
    )
    assert response.status_code == 302
    # The server-side source events should only come from newsletter_click, not server.
    assert not AnalyticsEvent.objects.filter(source="server").exists()
