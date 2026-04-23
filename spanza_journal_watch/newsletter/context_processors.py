from .cookies import has_subscribed_cookie


def subscriber_state(request):
    """Expose is_known_subscriber to templates for drawer/masthead state."""
    if getattr(request, "user", None) and request.user.is_authenticated:
        is_known = True
    else:
        is_known = has_subscribed_cookie(request)
    return {"is_known_subscriber": is_known}
