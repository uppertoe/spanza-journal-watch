"""Merging one author record into another."""

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from spanza_journal_watch.submissions.models import Author, HealthService
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture()
def manager_client():
    user = UserFactory()
    user.user_permissions.add(
        Permission.objects.get(codename="manage_issue_builder", content_type__app_label="submissions")
    )
    client = Client()
    client.force_login(user)
    return client


def _merge(client, source, target):
    return client.post(reverse("backend:author_merge", args=[source.pk]), {"into": target.pk})


def test_target_without_email_takes_the_source_email(manager_client):
    target = Author.objects.create(title="Dr", name="Andrew Hughes")
    source = Author.objects.create(title="Dr", name="Andrew Hughes", email="a.hughes@example.com")

    response = _merge(manager_client, source, target)

    assert response.status_code == 302
    assert not Author.objects.filter(pk=source.pk).exists()
    target.refresh_from_db()
    assert target.email == "a.hughes@example.com"


def test_target_keeps_its_own_email(manager_client):
    target = Author.objects.create(name="Target", email="target@example.com")
    source = Author.objects.create(name="Source", email="source@example.com")

    _merge(manager_client, source, target)

    target.refresh_from_db()
    assert target.email == "target@example.com"
    assert not Author.objects.filter(email="source@example.com").exists()


def test_health_services_are_combined(manager_client):
    service = HealthService.objects.create(name="Example Children's Hospital")
    target = Author.objects.create(name="Target")
    source = Author.objects.create(name="Source")
    source.health_services.add(service)

    _merge(manager_client, source, target)

    assert list(target.health_services.all()) == [service]


def test_survivor_takes_the_plain_slug_once_the_duplicate_is_gone(manager_client):
    original = Author.objects.create(title="Dr", name="Andrew Hughes")
    duplicate = Author.objects.create(title="Dr", name="Andrew Hughes")
    assert (original.slug, duplicate.slug) == ("andrew-hughes", "andrew-hughes-1")

    _merge(manager_client, original, duplicate)

    duplicate.refresh_from_db()
    assert duplicate.slug == "andrew-hughes"


def test_survivor_keeps_its_slug_when_the_plain_one_belongs_to_someone_else(manager_client):
    Author.objects.create(name="Frank Hsiao")
    keeper = Author.objects.create(name="Frank Hsiao")
    other = Author.objects.create(name="Someone Else")

    _merge(manager_client, other, keeper)

    keeper.refresh_from_db()
    assert keeper.slug == "frank-hsiao-1"
