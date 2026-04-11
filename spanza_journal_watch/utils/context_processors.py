from urllib.parse import urlparse

from django.conf import settings
from django.templatetags.static import static

from .cache import get_content_cache_version


def content_cache_version(request):
    media_origin = ""
    media_url = getattr(settings, "MEDIA_URL", "")
    if media_url.startswith("http"):
        parsed = urlparse(media_url)
        media_origin = f"{parsed.scheme}://{parsed.netloc}"

    return {
        "content_cache_version": get_content_cache_version(),
        "default_og_image": request.build_absolute_uri(static("images/logo/spanza-logo-blue.png")),
        "media_origin": media_origin,
    }
