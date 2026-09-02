"""IndexNow (https://www.indexnow.org/) URL submission.

Bing, Yandex, Seznam and others share the IndexNow protocol: POST a list of
changed URLs together with a site-owned key, and the engines re-crawl those
pages promptly instead of waiting for the sitemap to be re-read.

The key is proved by serving it as plain text at ``/<key>.txt`` (see
``layout.views.indexnow_key``). Submission is a no-op unless ``INDEXNOW_KEY``
is configured and DEBUG is off.
"""

import logging
from urllib.parse import urlsplit

import requests
from django.conf import settings

from .functions import get_domain_url

logger = logging.getLogger(__name__)

MAX_URLS_PER_REQUEST = 10_000
REQUEST_TIMEOUT = 10


def indexnow_enabled():
    return bool(getattr(settings, "INDEXNOW_KEY", "")) and not settings.DEBUG


def key_file_path():
    return f"/{settings.INDEXNOW_KEY}.txt"


def absolute_url(path):
    if path.startswith(("http://", "https://")):
        return path
    return f"{get_domain_url()}{path}"


def sitemap_paths():
    """Every path listed in the public sitemap, plus the home page."""
    from config.urls import sitemaps  # deferred: the URLconf imports views that import this module's callers

    paths = ["/"]
    for sitemap_class in sitemaps.values():
        sitemap = sitemap_class()
        paths.extend(sitemap.location(item) for item in sitemap.items())
    return list(dict.fromkeys(paths))


def submit_urls(paths):
    """POST ``paths`` (site-relative or absolute) to IndexNow.

    Returns the number of URLs accepted; 0 when submission is disabled or the
    endpoint rejected every batch. Network errors propagate as
    ``requests.RequestException`` so callers can retry.
    """
    if not indexnow_enabled():
        logger.debug("IndexNow disabled; not submitting %d path(s)", len(paths))
        return 0

    urls = list(dict.fromkeys(absolute_url(path) for path in paths))
    if not urls:
        return 0

    domain = get_domain_url()
    submitted = 0
    for start in range(0, len(urls), MAX_URLS_PER_REQUEST):
        batch = urls[start : start + MAX_URLS_PER_REQUEST]
        payload = {
            "host": urlsplit(domain).netloc,
            "key": settings.INDEXNOW_KEY,
            "keyLocation": f"{domain}{key_file_path()}",
            "urlList": batch,
        }
        response = requests.post(settings.INDEXNOW_ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code >= 400:
            logger.warning(
                "IndexNow rejected %d URL(s): HTTP %s %s",
                len(batch),
                response.status_code,
                response.text[:200],
            )
            continue
        submitted += len(batch)
    logger.info("IndexNow accepted %d of %d URL(s)", submitted, len(urls))
    return submitted
