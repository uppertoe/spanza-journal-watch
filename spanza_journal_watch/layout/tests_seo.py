"""
Tests for SEO template tags.

Covers:
1. breadcrumb_structured_data — emits BreadcrumbList JSON-LD from the request's crumbs
2. Nothing emitted with fewer than two crumbs or no request
3. Rendered tag page carries BreadcrumbList
"""

import json
import re

import pytest
from django.test import RequestFactory
from view_breadcrumbs.templatetags.view_breadcrumbs import CONTEXT_KEY

from spanza_journal_watch.layout.templatetags.seo_tags import breadcrumb_structured_data
from spanza_journal_watch.submissions.models import Tag


def _payload(html):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else None


class TestBreadcrumbStructuredData:
    def test_emits_list_with_absolute_urls(self):
        request = RequestFactory().get("/issues/march-2026")
        request.META[CONTEXT_KEY] = [
            ("Home", "/", (), {}),
            ("Issues", "/issues", (), {}),
            ("March 2026", "", (), {}),
        ]

        data = _payload(breadcrumb_structured_data({"request": request}))

        assert data["@type"] == "BreadcrumbList"
        items = data["itemListElement"]
        assert [i["position"] for i in items] == [1, 2, 3]
        assert items[0]["item"] == "http://testserver/"
        assert items[1]["item"] == "http://testserver/issues"
        assert items[2]["name"] == "March 2026"
        assert "item" not in items[2]

    def test_resolves_model_and_view_names(self, db):
        tag = Tag.objects.create(text="Airway")
        request = RequestFactory().get("/")
        request.META[CONTEXT_KEY] = [
            ("Home", "/", (), {}),
            (tag, tag, (), {}),
            ("Explore", "submissions:tag_list", (), {}),
        ]

        items = _payload(breadcrumb_structured_data({"request": request}))["itemListElement"]

        assert items[1]["item"] == f"http://testserver{tag.get_absolute_url()}"
        assert items[2]["item"].endswith("/explore")

    def test_single_crumb_emits_nothing(self):
        request = RequestFactory().get("/")
        request.META[CONTEXT_KEY] = [("Home", "/", (), {})]
        assert breadcrumb_structured_data({"request": request}) == ""

    def test_no_request_emits_nothing(self):
        assert breadcrumb_structured_data({}) == ""


@pytest.mark.django_db
def test_tag_page_renders_breadcrumb_list(client):
    tag = Tag.objects.create(text="Sedation")
    response = client.get(tag.get_absolute_url())
    assert response.status_code == 200
    scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)
    types = {json.loads(s)["@type"] for s in scripts}
    assert "BreadcrumbList" in types
