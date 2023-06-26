from django.db.models import Count
from django.http import HttpResponse
from django.template.loader import render_to_string

from spanza_journal_watch.submissions.models import Hit, Issue, Tag


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
        context["sidebar_issues"] = Issue.objects.exclude(active=False).order_by("-created")[
            : self.number_of_sidebar_issues
        ]
        context["sidebar_tags"] = (
            Tag.objects.exclude(active=False)
            .annotate(article_count=Count("articles"))
            .order_by("-article_count")[: self.number_of_tags]
        )
        return context
