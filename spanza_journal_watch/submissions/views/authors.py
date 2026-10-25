"""Contributor and health service pages."""

import json

from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.views.generic import ListView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import BaseBreadcrumbMixin

from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HitMixin, HtmxMixin, SidebarMixin

from ..models import Author, HealthService, Review, Tag
from .shared import (
    attach_review_display_fields,
    build_author_schema,
    build_paginated_canonical_url,
    build_request_absolute_url,
)


def _join(items):
    """'a', 'a and b', 'a, b and c', with a final comma when an item has its own 'and'."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    serial = "," if len(items) > 2 and any(" and " in item for item in items) else ""
    return f"{', '.join(items[:-1])}{serial} and {items[-1]}"


def _lower_first(text):
    # "Paediatric pain management" -> "paediatric pain management", but "COVID-19 and anaesthesia" stays.
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


class AuthorDetailView(
    AnonymousCacheMixin, HitMixin, BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, ListView
):
    model = Author
    template_name = "submissions/author_detail.html"

    # HTMX
    htmx_templates = [
        "layout/fragments/articles.html",
        "fragments/pagination.html",
        "fragments/action_dock_oob.html",
    ]

    # Frontend options
    paginate_by = 8
    article_cols = 1

    # Breadcrumb
    @cached_property
    def crumbs(self):
        return [("About", reverse("submissions:about")), (self.object, "")]

    def get(self, request, *args, **kwargs):
        # Gets the Author object from the url kwargs
        authors = Author.objects.exclude(anonymous=True)
        try:
            self.object = self.get_object(queryset=authors)
        except Http404:
            # Older addresses left out the honorific, e.g. /about/jane-citizen for Dr Jane Citizen.
            slug = kwargs.get("slug")
            match = next((author for author in authors.exclude(slug=slug) if slugify(author.name) == slug), None)
            if match is None:
                raise
            return HttpResponsePermanentRedirect(match.get_absolute_url())
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["article_cols"] = self.article_cols
        context["show_default_action_dock"] = False
        context["action_dock_aria_label"] = "Author page navigation"
        context["page_title"] = f"{self.object} | SPANZA Journal Watch"

        # The topics this author has reviewed, most reviewed first.
        topics = list(
            Tag.objects.filter(
                active=True, curated=True, articles__reviews__author=self.object, articles__reviews__active=True
            )
            .annotate(author_reviews=Count("articles__reviews", distinct=True))
            .order_by("-author_reviews", "display_order")
        )
        context["author_topics"] = topics
        review_count = context["paginator"].count
        services = [service.name for service in self.object.health_services.all()]
        who = f"{self.object}, of {_join(services)}," if services else str(self.object)
        summary = f"{who} has reviewed {review_count} paper{'s' if review_count != 1 else ''} for SPANZA Journal Watch"
        context["author_summary"] = f"{summary}."
        context["page_meta_description"] = (
            f"{summary}, on topics including {_join([_lower_first(t.get_display_name()) for t in topics[:3]])}."
            if topics
            else f"{summary}."
        )
        context["canonical_url"] = build_paginated_canonical_url(self.request, self.object.get_absolute_url())
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "ProfilePage",
                "url": context["canonical_url"],
                "mainEntity": {
                    **build_author_schema(self.request, self.object),
                    **({"knowsAbout": [topic.get_display_name() for topic in topics]} if topics else {}),
                },
            }
        )

        # Supply only paginated objects to the template
        paginator = context["paginator"]
        page = context["page_obj"]
        context["reviews"] = paginator.get_page(page.number)
        attach_review_display_fields(context["reviews"])

        return context

    def get_queryset(self):
        return (
            Review.objects.filter(author=self.object, active=True)
            .order_by("-created")
            .select_related("article__journal", "author")
            .prefetch_related("article__tags", "issues")
        )


class HealthServiceListView(AnonymousCacheMixin, BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, ListView):
    model = HealthService
    template_name = "submissions/healthservice_list.html"
    context_object_name = "health_services"

    # HTMX
    htmx_templates = ["submissions/fragments/healthservice_cards.html"]

    # Breadcrumb
    @cached_property
    def crumbs(self):
        return [("About", "")]

    def get_queryset(self):
        query = (self.request.GET.get("q") or "").strip()
        sort = (self.request.GET.get("sort") or "contributors").strip()

        authors_qs = (
            Author.objects.exclude(anonymous=True)
            .annotate(review_count=Count("reviews", filter=Q(reviews__active=True), distinct=True))
            .filter(review_count__gt=0)
            .order_by("name")
        )

        qs = (
            HealthService.objects.all()
            .annotate(
                contributor_count=Count("authors", filter=Q(authors__anonymous=False), distinct=True),
                review_count=Count("authors__reviews", filter=Q(authors__reviews__active=True), distinct=True),
            )
            .prefetch_related(Prefetch("authors", queryset=authors_qs))
        )

        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(authors__name__icontains=query)).distinct()

        if sort == "name":
            qs = qs.order_by("name")
        elif sort == "reviews":
            qs = qs.order_by("-review_count", "name")
        else:
            qs = qs.order_by("-contributor_count", "name")

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = (self.request.GET.get("q") or "").strip()
        context["sort"] = (self.request.GET.get("sort") or "contributors").strip()
        context["service_count"] = context["health_services"].count()
        context["page_title"] = "Contributors | SPANZA Journal Watch"
        context["page_meta_description"] = (
            "The paediatric anaesthetists who review the literature for SPANZA Journal Watch, "
            "and the hospitals and departments they work in across Australia and New Zealand."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:about"))
        if context["query"] or context["sort"] != "contributors":
            context["meta_robots"] = "noindex,follow"
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Contributors",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )
        return context
