"""Issues are listed by the date readers received them, not by the month they cover."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.submissions.models import Issue

pytestmark = pytest.mark.django_db


def _when(days_ago):
    return timezone.now() - datetime.timedelta(days=days_ago)


def _issue(name, month, active=True):
    return Issue.objects.create(name=name, body="body", date=month, active=active)


def _send(issue, days_ago, is_sent=True):
    return Newsletter.objects.create(issue=issue, subject=issue.name, is_sent=is_sent, send_date=_when(days_ago))


def test_newsletter_send_date_outranks_the_month_covered():
    special = _issue("Special edition", datetime.date(2026, 10, 1))
    monthly = _issue("November", datetime.date(2026, 7, 1))
    _send(special, days_ago=30)
    _send(monthly, days_ago=1)

    assert list(Issue.objects.in_release_order()) == [monthly, special]


def test_first_send_counts_not_a_later_resend():
    early = _issue("Early", datetime.date(2026, 1, 1))
    later = _issue("Later", datetime.date(2026, 2, 1))
    _send(early, days_ago=60)
    _send(early, days_ago=1)
    _send(later, days_ago=30)

    assert list(Issue.objects.in_release_order()) == [later, early]


def test_unsent_newsletter_is_ignored_and_publish_date_is_used():
    sent = _issue("Sent", datetime.date(2026, 9, 1))
    _send(sent, days_ago=10)
    new = _issue("Published, not yet emailed", datetime.date(2026, 3, 1))
    _send(new, days_ago=-5, is_sent=False)

    assert list(Issue.objects.in_release_order()) == [new, sent]


def test_published_at_is_set_on_first_publish_and_kept():
    issue = _issue("Draft", datetime.date(2026, 1, 1), active=False)
    assert issue.published_at is None

    issue.active = True
    issue.save(update_fields=["active", "modified"])
    issue.refresh_from_db()
    first = issue.published_at
    assert first is not None

    issue.active = False
    issue.save()
    issue.active = True
    issue.save()
    issue.refresh_from_db()
    assert issue.published_at == first


def test_latest_issue_redirect_follows_release_order(client):
    special = _issue("Special edition", datetime.date(2026, 10, 1))
    monthly = _issue("November", datetime.date(2026, 7, 1))
    _send(special, days_ago=30)
    _send(monthly, days_ago=1)

    response = client.get(reverse("submissions:issue_latest"))
    assert response.url == monthly.get_absolute_url()
