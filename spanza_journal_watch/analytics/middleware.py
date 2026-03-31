import uuid

VISITOR_COOKIE_NAME = "jwvid"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


class VisitorIdMiddleware:
    """
    Assigns a long-lived anonymous visitor ID cookie on first visit.
    The ID is attached to request.analytics_visitor_id for use in
    analytics event recording. No PII is stored — this is a UUID only.
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

        response = self.get_response(request)

        if getattr(request, "_set_visitor_cookie", False):
            from django.conf import settings

            response.set_cookie(
                VISITOR_COOKIE_NAME,
                visitor_id,
                max_age=VISITOR_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=not getattr(settings, "DEBUG", True),
            )
        return response
