from django.core.cache import cache

CONTENT_CACHE_VERSION_KEY = "content_cache_version"


def get_content_cache_version():
    version = cache.get(CONTENT_CACHE_VERSION_KEY)
    if version is None:
        cache.add(CONTENT_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def bump_content_cache_version():
    try:
        return cache.incr(CONTENT_CACHE_VERSION_KEY)
    except ValueError:
        cache.set(CONTENT_CACHE_VERSION_KEY, 2, timeout=None)
        return 2
