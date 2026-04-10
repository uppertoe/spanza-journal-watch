import uuid

from django.conf import settings

VISITOR_COOKIE_NAME = "jwvid"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year

_SHARE_TOKEN_PARAM = "ref"
_SHARE_TOKEN_SESSION_KEY = "analytics_share_token"
_LANDING_PAGE_SESSION_KEY = "analytics_landing_page"


def _session_exists(session, session_key):
    exists = getattr(session, "exists", None)
    if exists is None:
        return bool(session_key)
    return bool(session_key and exists(session_key))


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
