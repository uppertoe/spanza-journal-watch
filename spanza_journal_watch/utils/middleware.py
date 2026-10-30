from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.urls import Resolver404, resolve


class RemoveSlashMiddleware:
    """301 trailing-slash URLs to their canonical no-slash form.

    Site URLs are declared without trailing slashes, so inbound links like
    /reviews/some-slug/ would otherwise hard-404 (the inverse of Django's
    APPEND_SLASH). Only redirects when the slashed path doesn't resolve but
    the stripped path does, so slash-terminated routes (admin etc.) are
    untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path.endswith("/") and len(path) > 1:
            try:
                resolve(path)
            except Resolver404:
                stripped = path.rstrip("/")
                try:
                    resolve(stripped)
                except Resolver404:
                    pass
                else:
                    query = request.META.get("QUERY_STRING", "")
                    return HttpResponsePermanentRedirect(f"{stripped}?{query}" if query else stripped)
        return self.get_response(request)


# A sliding session is renewed at most this often, so most requests write nothing.
STAFF_SESSION_RENEW_INTERVAL = 60 * 60


class StaffSessionLimitMiddleware:
    """Give staff a session that ends after STAFF_SESSION_IDLE_AGE without a visit.

    Each request moves the expiry forward, so an editor who works on the site
    stays signed in, and an unattended session ends after a week. A session
    that ends when the browser closes (an unticked "Remember me") is left alone.
    allauth sets the expiry during sign-in, and this middleware runs after the
    view, so it has the last word.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if self._is_staff_session(request) and not request.session.get_expire_at_browser_close():
            idle_age = getattr(settings, "STAFF_SESSION_IDLE_AGE", 7 * 24 * 60 * 60)
            if not idle_age - STAFF_SESSION_RENEW_INTERVAL <= request.session.get_expiry_age() <= idle_age:
                request.session.set_expiry(idle_age)
        return response

    @staticmethod
    def _is_staff_session(request):
        # An empty session is checked first so anonymous requests never load a user or mark the session accessed.
        session = getattr(request, "session", None)
        if session is None or session.is_empty():
            return False
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_authenticated and user.is_staff)
