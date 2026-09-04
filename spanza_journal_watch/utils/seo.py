"""Small helpers for keeping crawlers on the pages that matter."""

from functools import wraps


def noindex_response(view):
    """Mark a view's responses noindex via the X-Robots-Tag header.

    For HTMX fragments and other endpoints that render partial HTML: they have no
    <head> to carry a robots meta tag, but crawlers still find them through
    hx-get attributes.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex"
        return response

    return wrapper
