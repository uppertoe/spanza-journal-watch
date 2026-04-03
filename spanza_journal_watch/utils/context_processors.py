from django.templatetags.static import static

from .cache import get_content_cache_version


def content_cache_version(request):
    return {
        "content_cache_version": get_content_cache_version(),
        "default_og_image": request.build_absolute_uri(static("images/logo/spanza-logo-blue.png")),
    }
