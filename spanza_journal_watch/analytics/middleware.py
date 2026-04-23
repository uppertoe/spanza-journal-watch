import logging
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)

VISITOR_COOKIE_NAME = "jwvid"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year

_SHARE_TOKEN_PARAM = "ref"
_SHARE_TOKEN_SESSION_KEY = "analytics_share_token"
_LANDING_PAGE_SESSION_KEY = "analytics_landing_page"

# Paths whose responses must never carry a session Set-Cookie.
# These are sub-resource or utility views served through the Django middleware
# stack.  If their responses include Set-Cookie (from SessionMiddleware), the
# cookie can race with or overwrite the authenticated session cookie during
# page transitions — especially after login, where cycle_key() has already
# deleted the old session.  See Django ticket #11506.
_NO_SESSION_COOKIE_PATHS = frozenset(
    [
        "/manifest.json",
        "/sw.js",
        "/robots.txt",
        "/healthz",
        "/site.webmanifest",
        "/offline.html",
        "/favicon.ico",
    ]
)

# Path prefixes that should also be excluded.  Covers root-level favicon
# files (android-chrome-*.png, apple-touch-icon.png, etc.) without needing
# to list every variant.
_NO_SESSION_COOKIE_EXTENSIONS = (".png", ".svg", ".xml", ".ico")


def _session_exists(session, session_key):
    exists = getattr(session, "exists", None)
    if exists is None:
        return bool(session_key)
    return bool(session_key and exists(session_key))


class SafeSessionCookieMiddleware:
    """
    Strip session Set-Cookie headers from responses that should not carry them.

    Django's SessionMiddleware has two branches that write Set-Cookie:

        DELETE:  if SESSION_COOKIE_NAME in request.COOKIES and session.is_empty()
        SET:     if (modified or SESSION_SAVE_EVERY_REQUEST) and not empty

    Setting ``request.session.modified = False`` only blocks the SET branch.
    The DELETE branch fires whenever a request arrives with a stale session
    cookie (e.g. after cycle_key() deleted it) and the session loads as empty —
    regardless of ``modified``.  This is Django ticket #11506 (open since 2013).

    This middleware strips the session cookie from responses for:
    - Views that set ``request._no_session_cookie = True``
    - Requests whose path is in ``_NO_SESSION_COOKIE_PATHS``

    Place this middleware **before** SessionMiddleware in the MIDDLEWARE list so
    that it processes the response **after** SessionMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        path = request.path
        should_strip = (
            getattr(request, "_no_session_cookie", False)
            or path in _NO_SESSION_COOKIE_PATHS
            or ("/" not in path.lstrip("/") and path.endswith(_NO_SESSION_COOKIE_EXTENSIONS))
        )

        if should_strip:
            cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
            if cookie_name in response.cookies:
                del response.cookies[cookie_name]

        return response


class VisitorIdMiddleware:
    """
    Assigns a long-lived anonymous visitor ID cookie on first visit.
    The ID is attached to request.analytics_visitor_id for use in
    analytics event recording. No PII is stored — this is a UUID only.

    Also captures:
    - Landing page path (first page in session)
    - Share token from ?ref= parameter (for share-to-visit attribution)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        visitor_id = request.COOKIES.get(VISITOR_COOKIE_NAME) or ""
        if not visitor_id:
            visitor_id = str(uuid.uuid4())
            request._set_visitor_cookie = True
        else:
            request._set_visitor_cookie = False
        request.analytics_visitor_id = visitor_id

        # Skip session access entirely for sub-resource paths.  Accessing the
        # session here would (a) add Vary: Cookie to the response, breaking
        # cacheability, and (b) risk triggering SessionMiddleware's DELETE
        # branch on stale cookies.
        path = request.path
        is_sub_resource = path in _NO_SESSION_COOKIE_PATHS or (
            "/" not in path.lstrip("/") and path.endswith(_NO_SESSION_COOKIE_EXTENSIONS)
        )
        if is_sub_resource:
            response = self.get_response(request)
            if getattr(request, "_set_visitor_cookie", False):
                response.set_cookie(
                    VISITOR_COOKIE_NAME,
                    visitor_id,
                    max_age=VISITOR_COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                    secure=not getattr(settings, "DEBUG", True),
                )
            return response

        session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
        has_session_cookie = bool(request.COOKIES.get(session_cookie_name))
        session_key = getattr(request.session, "session_key", None)
        has_persisted_session = _session_exists(request.session, session_key)

        # Capture landing page on first request in this session
        if (
            hasattr(request, "session")
            and _LANDING_PAGE_SESSION_KEY not in request.session
            and (has_persisted_session or not has_session_cookie)
        ):
            request.session[_LANDING_PAGE_SESSION_KEY] = request.path[:512]

        # Capture share attribution token from ?ref= parameter
        share_token = (request.GET.get(_SHARE_TOKEN_PARAM) or "").strip()[:32]
        if share_token and hasattr(request, "session") and (has_persisted_session or not has_session_cookie):
            request.session[_SHARE_TOKEN_SESSION_KEY] = share_token

        response = self.get_response(request)

        if getattr(request, "_set_visitor_cookie", False):
            response.set_cookie(
                VISITOR_COOKIE_NAME,
                visitor_id,
                max_age=VISITOR_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=not getattr(settings, "DEBUG", True),
            )
        return response


_PAGE_VISIT_EXCLUDED_PREFIXES = ("/editorial/", "/o/", "/__debug__/", "/tinymce/", "/markdownx/")


class PageVisitAnalyticsMiddleware:
    """
    Record a server-side PAGE_VISIT AnalyticsEvent for every full-page HTML GET.

    The JS-beacon events fire against the same origin, so their Referer header is
    always same-origin and categorises as "internal" — losing the real traffic
    source.  By emitting this event during request/response processing, we
    capture the true HTTP Referer before any client-side JS runs.

    Only fires for successful 2xx GET requests returning text/html that are not
    HTMX partials and not on staff/OAuth/debug paths.  The admin URL is
    configurable, so it's checked via settings.ADMIN_URL.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        from spanza_journal_watch.analytics.models import AnalyticsEvent

        self._AnalyticsEvent = AnalyticsEvent
        admin_url = (getattr(settings, "ADMIN_URL", "admin/") or "").strip("/")
        self._admin_prefix = f"/{admin_url}/" if admin_url else None

    def __call__(self, request):
        response = self.get_response(request)
        if self._should_record(request, response):
            try:
                self._AnalyticsEvent.record_event(
                    event_type=self._AnalyticsEvent.EventType.PAGE_VISIT,
                    request=request,
                    subscriber_id=request.session.get("subscriber_id"),
                    source="server",
                    metadata={"path": request.path[:512]},
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to record server-side PAGE_VISIT")
        return response

    def _should_record(self, request, response):
        if request.method != "GET":
            return False
        if not (200 <= response.status_code < 300):
            return False
        if request.headers.get("HX-Request", "").lower() == "true":
            return False
        content_type = (response.get("Content-Type") or "").lower()
        if not content_type.startswith("text/html"):
            return False
        path = request.path
        if self._admin_prefix and path.startswith(self._admin_prefix):
            return False
        if any(path.startswith(prefix) for prefix in _PAGE_VISIT_EXCLUDED_PREFIXES):
            return False
        return True
