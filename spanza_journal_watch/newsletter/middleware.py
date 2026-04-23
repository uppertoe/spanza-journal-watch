"""
Middleware that ensures authenticated subscribers carry the jw_sub flag
cookie, so that after logout the drawer masthead still shows "Profile"
rather than reverting to the anonymous "Subscribe" state.
"""

from django.conf import settings

from .cookies import JW_SUB_COOKIE_NAME, set_subscribed_cookie


class SubscriberCookieMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Early exits BEFORE calling get_response so we don't accidentally
        # touch request.user (which triggers session access and adds
        # Vary: Cookie to sub-resource responses that must stay cacheable).
        if request.COOKIES.get(JW_SUB_COOKIE_NAME) == "1":
            return self.get_response(request)
        session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
        if not request.COOKIES.get(session_cookie_name):
            return self.get_response(request)

        response = self.get_response(request)
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return response
        # Lazy import so migrations/startup don't load the ORM unnecessarily.
        from .models import Subscriber

        if Subscriber.objects.filter(user=user, subscribed=True).exists():
            set_subscribed_cookie(response)
        return response
