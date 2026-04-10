import uuid

from django.conf import settings

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
