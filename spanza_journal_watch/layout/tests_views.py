"""
Tests for layout views.

Covers:
1. manifest_view — returns JSON with correct content type and required PWA fields
2. service_worker_view — returns JS with correct headers
3. HomepageView — gracefully handles no Homepage object
"""

from django.test import TestCase
from django.urls import reverse


class TestManifestView(TestCase):
    def test_returns_json(self):
        response = self.client.get(reverse("layout:manifest"))
        assert response.status_code == 200
        assert "json" in response["Content-Type"]

    def test_contains_required_pwa_fields(self):
        response = self.client.get(reverse("layout:manifest"))
        data = response.json()
        assert "name" in data
        assert "icons" in data
        assert "start_url" in data
        assert "display" in data

    def test_cache_control_set(self):
        response = self.client.get(reverse("layout:manifest"))
        assert "max-age" in response.get("Cache-Control", "")


class TestServiceWorkerView(TestCase):
    def test_returns_javascript(self):
        response = self.client.get(reverse("layout:service_worker"))
        assert response.status_code == 200
        assert "javascript" in response["Content-Type"]

    def test_no_cache_header(self):
        response = self.client.get(reverse("layout:service_worker"))
        assert "no-cache" in response.get("Cache-Control", "")

    def test_service_worker_allowed_header(self):
        response = self.client.get(reverse("layout:service_worker"))
        assert response["Service-Worker-Allowed"] == "/"


class TestHomepageViewNoHomepage(TestCase):
    def test_renders_without_homepage(self):
        """Homepage should not crash when no Homepage object exists."""
        response = self.client.get("/")
        # May be 200 (empty page) or 200 with empty reviews — either way, no 500
        assert response.status_code == 200
