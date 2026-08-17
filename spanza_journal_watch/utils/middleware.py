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
