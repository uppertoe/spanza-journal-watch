from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from spanza_journal_watch.utils.cache import get_content_cache_version


def events_nav(request):
    """Expose whether the Events nav item should render.

    Visible only while an active session is upcoming or in progress; the
    events pages themselves stay reachable by URL either way.
    """
    version = get_content_cache_version()
    cache_key = f"events:nav_visible:v{version}"
    visible = cache.get(cache_key)
    if visible is None:
        from spanza_journal_watch.events.models import LiveSession

        now = timezone.now()
        visible = (
            LiveSession.objects.filter(active=True)
            .filter(Q(end_datetime__gte=now) | Q(end_datetime__isnull=True, start_datetime__gte=now))
            .exists()
        )
        cache.set(cache_key, visible, 60 * 30)
    return {"events_nav_visible": visible}
