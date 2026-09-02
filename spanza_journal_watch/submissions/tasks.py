import logging

import requests

from config.celery_app import app as celery_app
from spanza_journal_watch.utils import indexnow

logger = logging.getLogger(__name__)


@celery_app.task(autoretry_for=(requests.RequestException,), retry_backoff=60, max_retries=3)
def submit_urls_to_indexnow(paths):
    return indexnow.submit_urls(paths)


def queue_indexnow_submission(paths):
    """Queue an IndexNow ping without letting a broker outage break the caller."""
    if not indexnow.indexnow_enabled() or not paths:
        return
    try:
        submit_urls_to_indexnow.delay(list(paths))
    except Exception:  # noqa: BLE001 - never let search-engine pings break publishing
        logger.exception("Could not queue IndexNow submission for %d path(s)", len(paths))
