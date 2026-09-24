"""Gunicorn hooks: warm each worker before it takes traffic.

Django compiles a template the first time it is rendered and caches it per
process, so with two workers the first two visitors after every restart each
paid for that — measured on the production host at roughly 0.46 s against
0.23 s once warm. This renders the entry pages once inside each worker, after
it has loaded the application and before the arbiter puts it in the pool.

Deliberately `post_worker_init` and not `post_fork`: post_fork runs before the
worker has loaded the WSGI app, so Django would not be configured yet.

The request is made through the real public host, with the forwarded-proto
header the proxy sends, because `AnonymousCacheMixin` caches anonymous GETs
under a key built from the path alone — no host, no scheme. A warm request
made under a synthetic hostname, or over plain HTTP, would therefore store a
page whose absolute URLs point at that name or at `http://`, under the very
key a real visitor reads. Warming under the real host and scheme turns that
cache from a hazard into a bonus: the first genuine visitor gets a hit.

Set JW_WARM_ON_START=0 to disable.
"""

import os

# Pages to render into each worker. Keep this short: the point is to compile
# the templates a first visitor actually hits, not to touch every one of them.
WARM_PATHS = tuple(p for p in os.environ.get("JW_WARM_PATHS", "/").split(",") if p.strip())


def _public_host():
    """The first ALLOWED_HOSTS entry that looks like a real domain.

    ALLOWED_HOSTS carries internal names too (`jw-django`, `django`) for
    container-to-container calls; a warm request must not use one of those,
    because the response is cached under a host-blind key.
    """
    from django.conf import settings

    override = os.environ.get("JW_WARM_HOST")
    if override:
        return override
    for entry in settings.ALLOWED_HOSTS:
        host = entry.lstrip(".")
        if "." in host:
            return host
    return None


def post_worker_init(worker):
    if os.environ.get("JW_WARM_ON_START", "1") not in ("1", "true", "True"):
        return
    try:
        import time

        from django.db import connections
        from django.test import Client

        host = _public_host()
        if not host:
            worker.log.warning("warm-up skipped: no public host in ALLOWED_HOSTS")
            return

        client = Client(
            SERVER_NAME=host,
            HTTP_X_FORWARDED_PROTO="https",  # matches SECURE_PROXY_SSL_HEADER
            secure=True,
        )
        started = time.monotonic()
        results = []
        for path in WARM_PATHS:
            try:
                results.append(f"{path}={client.get(path).status_code}")
            except Exception as exc:  # one bad page must not cost us the rest
                results.append(f"{path}=ERR({type(exc).__name__})")
        elapsed = (time.monotonic() - started) * 1000
        worker.log.info("warmed %s in %.0f ms", " ".join(results), elapsed)

        # The warm request opened a connection in this worker; hand it back so
        # the worker starts with the same clean state as any other request
        # cycle would leave behind.
        for conn in connections.all():
            conn.close()
    except Exception as exc:
        # Never let warming stop a worker from serving.
        worker.log.warning("warm-up failed (%s: %s); serving anyway", type(exc).__name__, exc)
