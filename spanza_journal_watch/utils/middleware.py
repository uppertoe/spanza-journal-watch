import datetime
import time

from django.conf import settings
from django.contrib.auth import logout
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


STAFF_SIGNED_IN_AT_SESSION_KEY = "staff_signed_in_at"


class StaffSessionLimitMiddleware:
    """End a staff session STAFF_SESSION_COOKIE_AGE after sign-in, however the person signed in.

    The user_logged_in signal records the sign-in time, but allauth's password
    sign-in sets the session expiry again afterwards. This middleware runs after
    the view, so it has the last word on the expiry, and signs out a staff
    session that has outlived the limit. Sessions without a recorded time (such
    as a reader made staff while signed in) are timed from their next request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_staff_session(request) and self._deadline(request.session) <= time.time():
            logout(request)
        response = self.get_response(request)
        if self._is_staff_session(request):
            deadline = datetime.datetime.fromtimestamp(self._deadline(request.session), tz=datetime.UTC)
            if request.session.get_expiry_date() > deadline + datetime.timedelta(seconds=1):
                request.session.set_expiry(deadline)
        return response

    @staticmethod
    def _is_staff_session(request):
        # An empty session is checked first so anonymous requests never load a user or mark the session accessed.
        session = getattr(request, "session", None)
        if session is None or session.is_empty():
            return False
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_authenticated and user.is_staff)

    @staticmethod
    def _deadline(session):
        signed_in_at = session.get(STAFF_SIGNED_IN_AT_SESSION_KEY)
        if signed_in_at is None:
            signed_in_at = session[STAFF_SIGNED_IN_AT_SESSION_KEY] = time.time()
        return signed_in_at + getattr(settings, "STAFF_SESSION_COOKIE_AGE", 12 * 60 * 60)
