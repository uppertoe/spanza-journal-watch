import uuid

from django.conf import settings

VISITOR_COOKIE_NAME = "jwvid"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year

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


def ensure_visitor_id(request):
    """
    Return (visitor_id, created) for this request, minting a UUID if the
    jwvid cookie is absent.  Also attaches the ID to
    request.analytics_visitor_id so record_event picks it up.

    Only call this from uncacheable endpoints (the analytics beacon,
    newsletter click redirects) — the caller is responsible for setting the
    cookie on its response via set_visitor_cookie() when created is True.
    HTML page responses must never mint the cookie: they need to stay free of
    Set-Cookie so the CDN can cache them.
    """
    visitor_id = request.COOKIES.get(VISITOR_COOKIE_NAME) or ""
    created = False
    if not visitor_id:
        visitor_id = str(uuid.uuid4())
        created = True
    request.analytics_visitor_id = visitor_id
    return visitor_id, created


def set_visitor_cookie(response, visitor_id):
    response.set_cookie(
        VISITOR_COOKIE_NAME,
        visitor_id,
        max_age=VISITOR_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=not getattr(settings, "DEBUG", True),
    )


class VisitorIdMiddleware:
    """
    Expose the anonymous visitor ID cookie (if any) as
    request.analytics_visitor_id. No PII is stored — this is a UUID only.

    Read-only on purpose: the cookie is minted by the /reader/action beacon
    endpoint (see ensure_visitor_id), never on page responses, so that HTML
    stays free of Set-Cookie and remains edge-cacheable.  Landing page and
    share-token (?ref=) attribution are likewise captured client-side and
    arrive in the beacon payload rather than being written to the session.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.analytics_visitor_id = request.COOKIES.get(VISITOR_COOKIE_NAME) or None
        return self.get_response(request)
