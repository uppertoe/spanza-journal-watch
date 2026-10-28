"""Signing up with the newsletter box ticked subscribes only once the address is confirmed."""

import re
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse

from spanza_journal_watch.newsletter.models import Subscriber

pytestmark = pytest.mark.django_db

EMAIL = "new.member@example.org"


def _code(body):
    for line in body.splitlines():
        if re.fullmatch(r"\d{6}", line.strip()):
            return line.strip()
    raise AssertionError("no code found in email body")


def _sign_up(client, ticked=True):
    mail.outbox.clear()
    data = {"email": EMAIL}
    if ticked:
        data["subscribe_to_newsletter"] = "on"
    response = client.post(reverse("account_signup"), data)
    assert response.status_code == 302


def _enter_code(client):
    response = client.post(reverse("account_email_verification_sent"), {"code": _code(mail.outbox[-1].body)})
    assert response.status_code == 302


def test_ticked_box_subscribes_nobody_until_the_address_is_confirmed(client):
    _sign_up(client)
    assert not Subscriber.objects.filter(email__iexact=EMAIL).exists()

    _enter_code(client)

    subscriber = Subscriber.objects.get(email__iexact=EMAIL)
    assert subscriber.subscribed is True
    assert subscriber.source == Subscriber.Source.USER_SIGNUP
    assert subscriber.user.email == EMAIL
    assert client.session["subscribed"] is True
    assert "subscribe_on_confirm" not in client.session


def test_confirming_resubscribes_an_existing_address(client):
    existing = Subscriber.objects.create(email=EMAIL, subscribed=False)
    _sign_up(client)
    existing.refresh_from_db()
    assert existing.subscribed is False

    _enter_code(client)

    existing.refresh_from_db()
    assert existing.subscribed is True
    assert Subscriber.objects.filter(email__iexact=EMAIL).count() == 1


def test_unticked_box_never_subscribes(client):
    _sign_up(client, ticked=False)
    _enter_code(client)
    assert not Subscriber.objects.filter(email__iexact=EMAIL).exists()


def test_invited_member_is_subscribed_at_sign_up(client):
    # An invite link has already confirmed the address, so no code is asked for.
    with patch("spanza_journal_watch.users.adapters._matches_invite", return_value=True):
        _sign_up(client)
    subscriber = Subscriber.objects.get(email__iexact=EMAIL)
    assert subscriber.subscribed is True
    assert subscriber.source == Subscriber.Source.USER_SIGNUP
