"""The legacy site analytics page and the helpers the analytics panels share."""

import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.analytics.models import AnalyticsEvent

logger = logging.getLogger(__name__)


def _parse_iso_date(value):
    try:
        return datetime.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _safe_percentage(numerator, denominator):
    if not denominator:
        return "0%"
    return f"{round((numerator / denominator) * 100)}%"


def _site_analytics_rollout_date():
    """Date of the first JS-verified event. Fixed once it exists, so cached for an hour."""
    from django.core.cache import cache

    cache_key = "analytics:rollout_date"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    first_js_event = (
        AnalyticsEvent.objects.filter(js_verified=True)
        .order_by("timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    if not first_js_event:
        return None
    if timezone.is_aware(first_js_event):
        rollout = timezone.localtime(first_js_event).date()
    else:
        rollout = first_js_event.date()
    cache.set(cache_key, rollout, 3600)
    return rollout


def _newsletter_predates_site_analytics(newsletter):
    if not newsletter or not newsletter.send_date:
        return False
    rollout_date = _site_analytics_rollout_date()
    if rollout_date is None:
        return False
    send_date = timezone.localtime(newsletter.send_date).date() if timezone.is_aware(newsletter.send_date) else None
    if send_date is None:
        send_date = newsletter.send_date.date()
    return send_date < rollout_date


def _build_rate_row(*, label, opens=0, engaged=0, shares=0, full_text=0, searches=0, result_clicks=0, score=0):
    actions = shares + full_text
    return {
        "label": label,
        "opens": opens,
        "engaged": engaged,
        "shares": shares,
        "full_text": full_text,
        "actions": actions,
        "searches": searches,
        "result_clicks": result_clicks,
        "engaged_rate": _safe_percentage(engaged, opens),
        "share_rate": _safe_percentage(shares, opens),
        "full_text_ctr": _safe_percentage(full_text, opens),
        "action_rate": _safe_percentage(actions, engaged),
        "search_click_rate": _safe_percentage(result_clicks, searches),
        "score": score,
    }


@login_required
def analytics_redirect(request):
    return HttpResponseRedirect(reverse("backend:analytics_overview"))
