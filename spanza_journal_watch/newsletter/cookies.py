"""
Anonymous-subscriber flag cookie.

Separate from the jwvid analytics cookie so that jwvid stays anonymous
(no linkage between a pseudonymous visitor ID and a subscribe row).
"""

from django.conf import settings

JW_SUB_COOKIE_NAME = "jw_sub"
JW_SUB_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


def has_subscribed_cookie(request):
    return request.COOKIES.get(JW_SUB_COOKIE_NAME) == "1"


def set_subscribed_cookie(response):
    response.set_cookie(
        JW_SUB_COOKIE_NAME,
        "1",
        max_age=JW_SUB_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=not getattr(settings, "DEBUG", True),
    )
