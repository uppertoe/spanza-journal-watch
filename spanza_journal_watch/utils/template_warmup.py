"""Compile project templates into the cached loader at WSGI start-up.

Django's cached template loader parses each template on first use, so after
every deploy the first request per gunicorn worker for each page pays for the
parse (500-800 ms on the 1 vCPU production box). Compiling everything up front
moves that cost to worker boot, which the compose health check already waits for.
"""

import logging
import time

from django.conf import settings
from django.template import engines
from django.urls import NoReverseMatch, get_resolver, reverse

logger = logging.getLogger(__name__)


def warm_template_cache(template_dir=None):
    """Compile every ``.html`` under ``template_dir`` (default: the project templates dir).

    Returns the number of templates compiled. Never raises: a template that
    fails to parse is logged and skipped so a bad template can't block boot.
    """
    if not getattr(settings, "TEMPLATE_WARMUP", False):
        return 0
    template_dir = template_dir or settings.APPS_DIR / "templates"
    engine = engines["django"]
    started = time.monotonic()
    compiled = 0
    for path in sorted(template_dir.rglob("*.html")):
        name = path.relative_to(template_dir).as_posix()
        try:
            engine.get_template(name)
        except Exception:  # noqa: BLE001 - warm-up must never block start-up
            logger.warning("Template warm-up skipped %s", name, exc_info=True)
            continue
        compiled += 1
    logger.info("Template warm-up compiled %d templates in %.1fs", compiled, time.monotonic() - started)
    return compiled


def warm_url_resolver():
    """Import the URLconf and populate the reverse lookup tables.

    ``reverse()`` builds its lookup dicts lazily on first use; doing it at boot
    keeps that out of the first request too.
    """
    if not getattr(settings, "TEMPLATE_WARMUP", False):
        return False
    get_resolver().url_patterns  # noqa: B018 - property access imports the URLconf
    try:
        reverse("home")
    except NoReverseMatch:  # pragma: no cover - only if the root URL name changes
        pass
    return True
