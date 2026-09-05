"""Issue list, issue detail and the latest-issue redirect."""

import json

from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import ListView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import DetailBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HitMixin, HtmxMixin, SidebarMixin

from ..models import Issue, IssueSlugRedirect, Review
from .journal_browser import _attach_related_reviews_to_issue_page
from .shared import attach_review_display_fields, build_paginated_canonical_url, build_request_absolute_url


class IssueDetailView(
    AnonymousCacheMixin, HitMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, DetailBreadcrumbMixin, ListView
):
    template_name = "submissions/issue_detail.html"
    model = Issue

    # Breadcrumb
    breadcrumb_use_pk = False

    # HTMX
    htmx_templates = [
        "submissions/fragments/article_full.html",
        "submissions/fragments/issue_pagination.html",
        "submissions/fragments/contents_list_group.html",
        "submissions/fragments/issue_detail_action_dock_oob.html",
    ]

    # Frontend options
    paginate_by = 8
    article_cols = 1
    arrange_sidebar_top = True

    def get(self, request, *args, **kwargs):
        try:
            self.object = self.get_object(queryset=Issue.objects.exclude(active=False))
        except Http404:
            slug = self.kwargs.get(self.slug_url_kwarg, self.kwargs.get("slug"))
            redirect_entry = (
                IssueSlugRedirect.objects.filter(old_slug=slug, issue__active=True).select_related("issue").first()
            )
            if redirect_entry:
                return redirect(redirect_entry.issue.get_absolute_url(), permanent=True)
            raise
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue"] = self.object
        context["article_cols"] = self.article_cols
        context["page_title"] = f"{self.object.name} | SPANZA Journal Watch"
        context["page_meta_description"] = (
            f"{self.object.name}: curated SPANZA Journal Watch reviews and expert commentary "
            "on recent paediatric anaesthesia research, selected by SPANZA members."
        )
        context["canonical_url"] = build_paginated_canonical_url(self.request, self.object.get_absolute_url())
        issue_sd = {
            "@context": "https://schema.org",
            "@type": "PublicationIssue",
            "name": self.object.name,
            "url": build_request_absolute_url(self.request, self.object.get_absolute_url()),
            "description": context["page_meta_description"],
            "isPartOf": {
                "@type": "Periodical",
                "name": "SPANZA Journal Watch",
                "url": self.request.build_absolute_uri(reverse("submissions:issue_list")),
            },
        }
        if self.object.date:
            issue_sd["datePublished"] = self.object.date.isoformat()
        context["structured_data"] = json.dumps(issue_sd)

        # Rearrange the sidebar to ensure on top in mobile
        context["arrange_sidebar_top"] = self.arrange_sidebar_top

        # Override header
        override = {"title": self.object.name}
        header = PageHeader.get_active_for(PageHeader.PageType.ISSUE_DETAIL)
        context["page_header"] = header.collate_fields(**override) if header else override

        # Supply only paginated objects to the template
        paginator = context["paginator"]
        page = context["page_obj"]
        context["articles"] = paginator.get_page(page.number)
        attach_review_display_fields(
            context["articles"],
            issue=self.object,
            include_share_context=True,
            request=self.request,
        )

        # Batch-attach related reviews to each review on this page
        _attach_related_reviews_to_issue_page(context["articles"])

        return context

    def get_queryset(self):
        return (
            Review.objects.filter(issues=self.object, active=True)
            .order_by("-created")
            .select_related("article__journal", "author")
            .prefetch_related("article__tags", "issues")
        )


class IssueListView(AnonymousCacheMixin, SidebarMixin, HtmxMixin, ListBreadcrumbMixin, ListView):
    model = Issue
    context_object_name = "issues"
    template_name = "submissions/issue_list.html"
    # The card fragment counts each issue's reviews; prefetch them so the page
    # runs one query for all issues instead of two per issue.
    queryset = Issue.objects.exclude(active=False).order_by("-date").prefetch_related("reviews")

    # HTMX
    htmx_templates = [
        "submissions/fragments/issues.html",
        "submissions/fragments/issue_list_pagination.html",
        "fragments/action_dock_oob.html",
    ]

    # Frontend options
    paginate_by = 5
    issue_cols = 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        Issue.attach_display_images(context["issues"])
        context["issue_cols"] = self.issue_cols
        context["show_default_action_dock"] = True
        context["action_dock_aria_label"] = "Issue list quick navigation"
        context["page_title"] = "Issues | SPANZA Journal Watch"
        context["page_meta_description"] = (
            "Browse every SPANZA Journal Watch issue: curated collections of paediatric anaesthesia "
            "literature reviews with expert commentary, published regularly by SPANZA members."
        )
        context["canonical_url"] = build_paginated_canonical_url(self.request, reverse("submissions:issue_list"))
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Issues",
                "url": build_request_absolute_url(self.request, reverse("submissions:issue_list")),
                "description": context["page_meta_description"],
            }
        )

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.ISSUE_LIST)
        override = {}
        context["page_header"] = header.collate_fields(**override) if header else override

        return context


class LatestIssueView(RedirectView):
    permanent = False
    query_string = False

    def get_redirect_url(self, *args, **kwargs):
        issue = Issue.objects.exclude(active=False).order_by("-date").first()
        if not issue:
            raise Http404
        return issue.get_absolute_url()
