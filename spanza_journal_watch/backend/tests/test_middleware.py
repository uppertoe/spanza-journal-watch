"""
Tests for HtmxMessagesMiddleware.

Covers:
1. Non-HTMX requests pass through unchanged
2. HTMX requests — middleware processes without error
3. Non-200 HTMX responses are not modified
4. Unit test of middleware logic with mocked request/response
"""

import pytest
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory

from spanza_journal_watch.backend.middleware import HtmxMessagesMiddleware

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture()
def chief_editor(db):
    from django.contrib.auth.models import Permission

    user = User.objects.create_user(email="middleware-editor@example.com", password="pw", is_staff=True)
    for codename in ("chief_editor", "manage_issue_builder"):
        perm = Permission.objects.get(codename=codename)
        user.user_permissions.add(perm)
    return user


class TestHtmxMessagesMiddleware:
    def test_non_htmx_request_passes_through(self):
        """Non-HTMX requests are not modified by the middleware."""
        factory = RequestFactory()
        request = factory.get("/")
        # No HX-Request header — middleware should pass through
        inner_response = HttpResponse("<p>full page</p>", content_type="text/html")
        middleware = HtmxMessagesMiddleware(lambda r: inner_response)
        response = middleware(request)
        assert response.content == b"<p>full page</p>"

    def test_htmx_non_200_not_modified(self, client, chief_editor):
        """The middleware should not modify non-200 HTMX responses."""
        client.force_login(chief_editor)
        response = client.get(
            "/nonexistent-url-for-test/",
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404

    def test_skips_non_htmx_request(self):
        """Middleware skips requests without HX-Request header."""
        factory = RequestFactory()
        request = factory.get("/")

        inner_response = HttpResponse("<p>content</p>", content_type="text/html")

        middleware = HtmxMessagesMiddleware(lambda r: inner_response)
        response = middleware(request)

        assert response.content == b"<p>content</p>"

    def test_skips_non_html_htmx_response(self):
        """Middleware skips HTMX responses with non-HTML content type."""
        factory = RequestFactory()
        request = factory.get("/", HTTP_HX_REQUEST="true")

        inner_response = HttpResponse('{"ok": true}', content_type="application/json")

        middleware = HtmxMessagesMiddleware(lambda r: inner_response)
        response = middleware(request)

        assert response.content == b'{"ok": true}'

    def test_skips_when_messages_already_consumed(self):
        """Middleware does not inject OOB when messages were already consumed by the template."""
        factory = RequestFactory()
        request = factory.get("/", HTTP_HX_REQUEST="true")
        request.user = User.objects.create_user(email="msg-consumed@example.com", password="pw")

        from django.contrib.messages.storage.cookie import CookieStorage

        storage = CookieStorage(request)
        storage.used = True
        request._messages = storage

        inner_response = HttpResponse("<p>partial</p>", content_type="text/html")

        middleware = HtmxMessagesMiddleware(lambda r: inner_response)
        response = middleware(request)

        assert response.content == b"<p>partial</p>"

    def test_appends_oob_when_messages_unconsumed(self):
        """Middleware appends OOB fragment when HTMX request has unconsumed messages."""
        factory = RequestFactory()
        request = factory.get("/", HTTP_HX_REQUEST="true")
        request.user = User.objects.create_user(email="msg-unconsumed@example.com", password="pw")
        request.session = {}

        from django.contrib.messages.storage.cookie import CookieStorage

        storage = CookieStorage(request)
        storage.add(messages.SUCCESS, "Test message")
        request._messages = storage

        inner_response = HttpResponse("<p>partial</p>", content_type="text/html")

        middleware = HtmxMessagesMiddleware(lambda r: inner_response)
        response = middleware(request)

        content = response.content.decode()
        assert "Test message" in content
        assert len(content) > len("<p>partial</p>")
