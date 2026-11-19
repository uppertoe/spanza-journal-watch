"""Unsubscribe requests sent by email to the List-Unsubscribe mailto address."""

from types import SimpleNamespace

import pytest

from spanza_journal_watch.newsletter.inbound import UNSUBSCRIBE_ADDRESS, handle_unsubscribe_email
from spanza_journal_watch.newsletter.models import Subscriber

pytestmark = pytest.mark.django_db

PASS = {"status": "PASS"}
FAIL = {"status": "FAIL"}


def _message(from_email, envelope_sender=None, recipient=UNSUBSCRIBE_ADDRESS):
    return SimpleNamespace(
        from_email=from_email,
        envelope_sender=envelope_sender or from_email,
        envelope_recipient=recipient,
    )


def _receipt(dmarc=PASS, spf=PASS):
    return {"receipt": {"dmarcVerdict": dmarc, "spfVerdict": spf}}


@pytest.fixture()
def subscriber():
    return Subscriber.objects.create(email="kelly@example.org", subscribed=True)


def test_authenticated_request_unsubscribes_the_sender(subscriber):
    handled = handle_unsubscribe_email(_message('"Shepherd, Kelly" <Kelly@Example.org>'), _receipt())

    assert handled is True
    subscriber.refresh_from_db()
    assert subscriber.subscribed is False
    assert subscriber.unsubscribed_at is not None


def test_spf_pass_with_matching_envelope_sender_is_enough(subscriber):
    message = _message("kelly@example.org", envelope_sender="kelly@example.org")

    assert handle_unsubscribe_email(message, _receipt(dmarc=FAIL, spf=PASS)) is True
    subscriber.refresh_from_db()
    assert subscriber.subscribed is False


def test_forged_sender_is_left_for_the_inbox(subscriber):
    message = _message("kelly@example.org", envelope_sender="someone@elsewhere.example")

    assert handle_unsubscribe_email(message, _receipt(dmarc=FAIL, spf=PASS)) is False
    subscriber.refresh_from_db()
    assert subscriber.subscribed is True


def test_unknown_sender_is_left_for_the_inbox():
    assert handle_unsubscribe_email(_message("stranger@example.org"), _receipt()) is False


def test_mail_to_other_addresses_is_not_touched(subscriber):
    message = _message("kelly@example.org", recipient="editor@journalwatch.org.au")

    assert handle_unsubscribe_email(message, _receipt()) is False
    subscriber.refresh_from_db()
    assert subscriber.subscribed is True


def test_repeat_request_keeps_the_first_unsubscribe_date(subscriber):
    handle_unsubscribe_email(_message("kelly@example.org"), _receipt())
    subscriber.refresh_from_db()
    first = subscriber.unsubscribed_at

    assert handle_unsubscribe_email(_message("kelly@example.org"), _receipt()) is True
    subscriber.refresh_from_db()
    assert subscriber.unsubscribed_at == first
