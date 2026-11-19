"""Old author addresses forward to the author's current page."""

import pytest
from django.urls import reverse

from spanza_journal_watch.submissions.models import Author

pytestmark = pytest.mark.django_db


def _about(slug):
    return reverse("submissions:author_detail", kwargs={"slug": slug})


def test_retired_numbered_slug_redirects_to_the_plain_one(client):
    author = Author.objects.create(title="Dr", name="Andrew Hughes")
    assert author.slug == "andrew-hughes"

    response = client.get(_about("andrew-hughes-1"))

    assert response.status_code == 301
    assert response.url == author.get_absolute_url()


def test_numbered_slug_still_in_use_is_served_directly(client):
    Author.objects.create(name="Frank Hsiao")
    second = Author.objects.create(name="Frank Hsiao")

    response = client.get(_about(second.slug))

    assert response.status_code == 200


def test_unknown_slug_is_not_found(client):
    Author.objects.create(name="Andrew Hughes")

    assert client.get(_about("someone-else-1")).status_code == 404
