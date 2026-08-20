import json

from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import DetailView, ListView
from view_breadcrumbs import BaseBreadcrumbMixin

from spanza_journal_watch.submissions.views import build_request_absolute_url
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin

from .models import LiveSession

LIVE_SERIES_TITLE = "PACS by SPANZA"
LIVE_SERIES_DESCRIPTION = (
    "PACS by SPANZA – Journal Watch Edition: three interactive one-hour online sessions "
    "bringing the latest evidence in paediatric anaesthesia to life, centred on discussion "
    "with experienced clinicians."
)


class LiveSessionListView(AnonymousCacheMixin, BaseBreadcrumbMixin, ListView):
    model = LiveSession
    context_object_name = "sessions"
    template_name = "events/session_list.html"
    queryset = LiveSession.objects.filter(active=True)

    @cached_property
    def crumbs(self):
        return [("Live", "")]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = f"{LIVE_SERIES_TITLE} | SPANZA Journal Watch"
        context["page_meta_description"] = LIVE_SERIES_DESCRIPTION
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("events:session_list"))
        context["registration"] = next(
            (session for session in context["sessions"] if session.registration_url and not session.is_past),
            None,
        )
        return context


class LiveSessionDetailView(AnonymousCacheMixin, BaseBreadcrumbMixin, DetailView):
    model = LiveSession
    context_object_name = "session"
    template_name = "events/session_detail.html"
    queryset = LiveSession.objects.filter(active=True)

    @cached_property
    def crumbs(self):
        return [("Live", reverse("events:session_list")), (self.object, "")]

    def get_structured_data(self, canonical_url):
        session = self.object
        event = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": session.title,
            "startDate": session.start_datetime.isoformat(),
            "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": {
                "@type": "VirtualLocation",
                "url": session.event_url or canonical_url,
            },
            "organizer": {
                "@type": "Organization",
                "name": "Society for Paediatric Anaesthesia in New Zealand and Australia",
                "url": "https://www.spanza.org.au/",
            },
            "url": canonical_url,
        }
        if session.end_datetime:
            event["endDate"] = session.end_datetime.isoformat()
        description = session.get_truncated_description()
        if description:
            event["description"] = description
        if session.speaker:
            speakers = [name.strip() for name in session.speaker.split(" · ") if name.strip()]
            performers = [{"@type": "Person", "name": name} for name in speakers]
            event["performer"] = performers[0] if len(performers) == 1 else performers
        return json.dumps(event)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.object

        context["readings"] = list(session.readings.select_related("review__article__journal", "article__journal"))

        canonical_url = build_request_absolute_url(self.request, session.get_absolute_url())
        context["canonical_url"] = canonical_url
        context["page_title"] = f"{session.title} | {LIVE_SERIES_TITLE}"
        context["page_meta_description"] = session.get_truncated_description() or LIVE_SERIES_DESCRIPTION
        context["structured_data"] = self.get_structured_data(canonical_url)
        return context
