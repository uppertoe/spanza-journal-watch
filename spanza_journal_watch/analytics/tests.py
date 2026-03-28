from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from spanza_journal_watch.analytics.utils import (
    NEWSLETTER_AUTOMATION_WINDOW,
    is_probable_automated_newsletter_event,
)
from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.submissions.models import Issue


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


def test_newsletter_event_long_after_send_is_not_automated_without_markers():
    newsletter = _make_newsletter(send_date=timezone.now() - NEWSLETTER_AUTOMATION_WINDOW - timedelta(seconds=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")

    assert is_probable_automated_newsletter_event(request, newsletter) is False


def test_newsletter_event_with_scanner_user_agent_is_automated():
    newsletter = _make_newsletter(send_date=timezone.now() - timedelta(hours=1))
    request = RequestFactory().get("/", HTTP_USER_AGENT="GoogleImageProxy")

    assert is_probable_automated_newsletter_event(request, newsletter) is True
