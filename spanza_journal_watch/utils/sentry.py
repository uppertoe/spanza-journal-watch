"""Remove email addresses from Sentry events and breadcrumbs before they are sent.

Log messages and exception text sometimes carry a reader's address (a failed
send, a validation error). send_default_pii=False does not cover those, so
these hooks replace any address with a placeholder.
"""

import re

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
REDACTED_EMAIL = "[email]"


def redact_emails(value):
    """Return ``value`` with email addresses replaced, recursing into dicts, lists and tuples."""
    if isinstance(value, str):
        return EMAIL_PATTERN.sub(REDACTED_EMAIL, value)
    if isinstance(value, dict):
        return {key: redact_emails(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_emails(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_emails(item) for item in value)
    return value


def before_send(event, hint):
    if "message" in event:
        event["message"] = redact_emails(event["message"])
    if "logentry" in event:
        event["logentry"] = redact_emails(event["logentry"])
    for exception in (event.get("exception") or {}).get("values") or []:
        if "value" in exception:
            exception["value"] = redact_emails(exception["value"])
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and "values" in breadcrumbs:
        breadcrumbs["values"] = [before_breadcrumb(crumb, None) for crumb in breadcrumbs["values"]]
    return event


def before_breadcrumb(crumb, hint):
    if "message" in crumb:
        crumb["message"] = redact_emails(crumb["message"])
    if "data" in crumb:
        crumb["data"] = redact_emails(crumb["data"])
    return crumb
