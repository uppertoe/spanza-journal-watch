"""Request checks that refuse scripted sign-ups before an account or email is created.

The rules come from the fleet of 2026-11-23, which posted to /accounts/signup/
from fresh IPv6 addresses at hosting providers, a few minutes apart:

- It sent ``Accept-Language: en-US, en; q=0.9``. Browsers never put a space
  before ``q=``, so that request was written by a script.
- It claimed Chrome 133 or 134 without the ``Priority`` header. Chrome sends
  ``Priority`` on every page load, and real readers run a current version.
- Many of its addresses shared a /64, which normally belongs to one household
  or one phone.
"""

import ipaddress
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from spanza_journal_watch.analytics.utils import chromium_major, expected_chromium_major

logger = logging.getLogger(__name__)

# A claimed Chrome this many majors behind the current release, without the Priority header.
STALE_CHROMIUM_MAJORS_BEHIND = 15

_SPACED_QUALITY_RE = re.compile(r";\s+q=")

SCRIPTED_ACCEPT_LANGUAGE = "scripted_accept_language"
STALE_CHROMIUM_WITHOUT_PRIORITY = "stale_chromium_without_priority"


def scripted_signup_reason(request):
    """Return a reason token when the request was not made by a real browser, else None."""
    if _SPACED_QUALITY_RE.search(request.headers.get("accept-language") or ""):
        return SCRIPTED_ACCEPT_LANGUAGE
    major = chromium_major((request.headers.get("user-agent") or "").lower())
    if (
        major is not None
        and major < expected_chromium_major() - STALE_CHROMIUM_MAJORS_BEHIND
        and not request.headers.get("priority")
    ):
        return STALE_CHROMIUM_WITHOUT_PRIORITY
    return None


def ipv6_prefix(ip):
    """Return the /64 holding an IPv6 address, or None for IPv4 and unreadable addresses.

    IPv4 is left out because a hospital or university puts many readers behind one address.
    """
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    if address.version != 6 or address.ipv4_mapped:
        return None
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


def ipv6_prefix_signup_allowed(ip):
    """Cap new accounts from one IPv6 /64 per day."""
    prefix = ipv6_prefix(ip)
    if prefix is None:
        return True
    limit = getattr(settings, "SIGNUP_DAILY_LIMIT_PER_IPV6_PREFIX", 1)
    key = f"users:new-signups-v6:{timezone.now():%Y%m%d}:{prefix}"
    cache.add(key, 0, timeout=26 * 60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:
        # The dummy cache used in local development keeps nothing.
        return True
    return count <= limit


def log_refusal(reason):
    """Record a refused sign-up by reason only; the address is not logged."""
    logger.info("Sign-up refused: %s", reason)
