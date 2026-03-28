from django.core.cache import cache
from django.db.models import Count
from django.http import HttpResponse
from django.template.loader import render_to_string

from spanza_journal_watch.analytics.models import PageView
from spanza_journal_watch.analytics.utils import is_probable_automated_event
from spanza_journal_watch.submissions.models import Hit, Issue, Tag
from spanza_journal_watch.utils.cache import get_content_cache_version


class HtmxMixin:
    """
    If an HTMX request is made, takes a ["templates"] and returns
    concactenated, context-filled HTML as an HTMX response
    """

    htmx_templates = []

    def render_htmx_response(self):
        context = self.get_context_data()
        response = []
        for template in self.htmx_templates:
            response.append(render_to_string(template, context, request=self.request))
        return HttpResponse("".join(response))

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("HX-Request") == "true":
            return self.render_htmx_response()
        return super().render_to_response(context, **response_kwargs)


class HitMixin:
    """
    Takes the obj and stores it in the session
    in the form of {obj.model_name: obj.id}
    If an obj.id is not present, call Hit.update_page_count
    """

    def get_object(self, **kwargs):
        obj = super().get_object(**kwargs)

        # All views recorded in PageView
        subscriber_id = self.request.session.get("subscriber_id")
        PageView.record_view(obj, subscriber_id, request=self.request)

        # Keep human-facing hit counters resilient to scanners/prefetchers
        if is_probable_automated_event(self.request):
            return obj

        # Only unique hits recorded
        model_class = str(obj.__class__.__name__).lower()
        model_str = f"model_{model_class}_viewed"
        viewed_objects = self.request.session.get(model_str, [])

        if obj.id not in viewed_objects:
            Hit.update_page_count(obj)
            viewed_objects.append(obj.id)

        self.request.session[model_str] = viewed_objects

        return obj


class SidebarMixin:
    """
    Adds sidebar features to the context
    """

    # Layout variables
    number_of_sidebar_issues = 3
    number_of_tags = 8

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cache_version = get_content_cache_version()

        issues_cache_key = f"sidebar_issues:v{cache_version}:n{self.number_of_sidebar_issues}"
        tags_cache_key = f"sidebar_tags:v{cache_version}:n{self.number_of_tags}"

        context["sidebar_issues"] = cache.get_or_set(
            issues_cache_key,
            lambda: list(Issue.objects.exclude(active=False).order_by("-date")[: self.number_of_sidebar_issues]),
            timeout=60 * 30,
        )
        context["sidebar_tags"] = cache.get_or_set(
            tags_cache_key,
            lambda: list(
                Tag.objects.exclude(active=False)
                .annotate(article_count=Count("articles"))
                .order_by("-article_count")[: self.number_of_tags]
            ),
            timeout=60 * 30,
        )
        Issue.attach_display_images(context["sidebar_issues"])
        return context


class GetLatestInstanceMixin:
    """
    Gets the last modified active instance for a model
    Requires an 'active' and 'created' field
    """

    @classmethod
    def get_latest_instance(cls):
        return cls.objects.exclude(active=False).order_by("-modified").first()
