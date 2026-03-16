from .cache import get_content_cache_version


def content_cache_version(request):
    return {"content_cache_version": get_content_cache_version()}
