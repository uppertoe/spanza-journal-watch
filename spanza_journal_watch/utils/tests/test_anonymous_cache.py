from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.views import View

from spanza_journal_watch.utils.mixins import AnonymousCacheMixin

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "anon-cache-test"}}


class CountingView(AnonymousCacheMixin, View):
    renders = 0

    def get(self, request):
        CountingView.renders += 1
        return HttpResponse(f"render {CountingView.renders}")


def _get(cookies=None):
    request = RequestFactory().get("/cached-page")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.COOKIES.update(cookies or {})
    return CountingView.as_view()(request)


@override_settings(CACHES=LOCMEM)
def test_page_with_waiting_message_is_neither_cached_nor_served_from_cache():
    from django.core.cache import cache

    cache.clear()
    CountingView.renders = 0

    first = _get()
    with_message = _get(cookies={"messages": "pending"})
    after = _get()

    assert first.content == b"render 1"
    assert with_message.content == b"render 2"
    # The page rendered with a message was not stored for the next visitor
    assert after.content == b"render 1"
