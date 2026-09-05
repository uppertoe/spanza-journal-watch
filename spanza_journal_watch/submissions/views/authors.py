"""Contributor and health service pages."""

import json

from django.db.models import Count, Prefetch, Q
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import ListView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import BaseBreadcrumbMixin

from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HitMixin, HtmxMixin, SidebarMixin

from ..models import Author, HealthService, Review
from .shared import attach_review_display_fields, build_paginated_canonical_url, build_request_absolute_url


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
        self.object = self.get_object(queryset=Author.objects.exclude(anonymous=True))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["article_cols"] = self.article_cols
        context["show_default_action_dock"] = False
        context["action_dock_aria_label"] = "Author page navigation"
        context["page_title"] = f"{self.object} | SPANZA Journal Watch"
        context["page_meta_description"] = (
            f"Paediatric anaesthesia literature reviews by {self.object} for SPANZA Journal Watch: "
            "summaries and expert commentary on recent research of interest to clinicians."
        )
        context["canonical_url"] = build_paginated_canonical_url(self.request, self.object.get_absolute_url())
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "ProfilePage",
                "url": context["canonical_url"],
                "mainEntity": {
                    "@type": "Person",
                    "name": str(self.object),
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
