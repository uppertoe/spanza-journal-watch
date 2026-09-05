"""The public search page."""

import json

from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import ListView
from view_breadcrumbs import BaseBreadcrumbMixin

from spanza_journal_watch.analytics.models import AnalyticsEvent
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HtmxMixin, SidebarMixin

from ..models import Review, Tag
from .shared import attach_review_display_fields, build_request_absolute_url


class SearchView(AnonymousCacheMixin, BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, ListView):
    template_name = "submissions/search.html"
    context_object_name = "result_reviews"
    paginate_by = 20

    # HTMX
    htmx_templates = [
        "submissions/fragments/search_results.html",
        "submissions/fragments/search_pagination.html",
    ]

    # Search settings
    no_result_message = "No results found"

    # Breadcrumb
    @cached_property
    def crumbs(self):
        return [("Search", reverse("submissions:search"))]

    def get_queryset(self):
        query = (self.request.GET.get("q") or "").strip()
        selected_year = (self.request.GET.get("year") or "").strip()
        selected_tags = [slug for slug in self.request.GET.getlist("tag") if slug]

        if not selected_tags:
            comma_tags = (self.request.GET.get("tags") or "").strip()
            if comma_tags:
                selected_tags = [slug.strip() for slug in comma_tags.split(",") if slug.strip()]

        self._query = query
        self._selected_year = selected_year
        self._selected_tags = selected_tags
        # Empty state: no query and no filters → return nothing
        if not any([query, selected_year, selected_tags]):
            return Review.objects.none()

        if query:
            reviews = Review.search(query)
        else:
            reviews = (
                Review.objects.exclude(active=False)
                .select_related("article__journal", "author")
                .prefetch_related("article__tags", "issues")
                .order_by("-created")
            )

        if selected_year and str(selected_year).isdigit():
            reviews = reviews.filter(publish_date__year=int(selected_year))

        if selected_tags:
            tag_ids = list(Tag.objects.filter(slug__in=selected_tags).values_list("id", flat=True))
            if tag_ids:
                TagArticle = Tag.articles.through
                # EXISTS avoids M2M join fan-out; don't replace with .filter(article__tags__slug__in=...).distinct()
                reviews = reviews.filter(
                    Exists(TagArticle.objects.filter(pubmedarticle_id=OuterRef("article_id"), tag_id__in=tag_ids))
                )

        return reviews

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query = self._query
        selected_year = self._selected_year
        selected_tags = self._selected_tags
        has_filters = any([query, selected_year, selected_tags])

        # Post-process search headlines and attach display fields
        page_reviews = list(context["result_reviews"])
        if query:
            Review.post_process_headlines(page_reviews)
        attach_review_display_fields(page_reviews)
        context["result_reviews"] = page_reviews

        result_count = context["paginator"].count if has_filters else 0
        context["result_count"] = result_count
        context["is_browse_mode"] = has_filters and not query
        context["has_filters"] = has_filters

        if has_filters and result_count == 0:
            context["no_result_message"] = self.no_result_message

        context["query"] = query
        context["selected_year"] = selected_year
        context["selected_tags"] = selected_tags
        context["page_title"] = "Search | SPANZA Journal Watch"
        context["page_meta_description"] = (
            "Search SPANZA Journal Watch reviews by title, author, journal, year, and topic."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:search"))
        context["meta_robots"] = "noindex,follow"
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "SearchResultsPage",
                "name": "Search",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )

        cache_version = get_content_cache_version()
        year_options_key = f"search_year_options:v{cache_version}"
        tag_options_key = f"search_tag_options:v{cache_version}"

        context["year_options"] = cache.get_or_set(
            year_options_key,
            lambda: [
                date_obj.year
                for date_obj in Review.objects.exclude(active=False)
                .exclude(publish_date__isnull=True)
                .dates("publish_date", "year", order="DESC")
            ],
            timeout=60 * 30,
        )
        context["tag_options"] = cache.get_or_set(
            tag_options_key,
            lambda: list(
                Tag.objects.filter(active=True, curated=True)
                .annotate(
                    review_count=Count(
                        "articles__reviews",
                        filter=Q(articles__reviews__active=True),
                        distinct=True,
                    )
                )
                .filter(review_count__gt=0)
                .order_by("-review_count", "text")
            ),
            timeout=60 * 30,
        )

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.SEARCH)
        override = {}
        context["page_header"] = header.collate_fields(**override) if header else override

        # Build query string for pagination links (preserving filters)
        params = self.request.GET.copy()
        params.pop("page", None)
        context["filter_query_string"] = params.urlencode()

        self._record_search_event(
            query=query,
            selected_year=selected_year,
            selected_tags=selected_tags,
            result_count=result_count,
            is_browse_mode=context["is_browse_mode"],
        )

        return context

    def _record_search_event(self, *, query, selected_year, selected_tags, result_count, is_browse_mode):
        if not any([query, selected_year, selected_tags]):
            return

        signature = json.dumps(
            {
                "query": query,
                "year": selected_year,
                "tags": selected_tags,
            },
            sort_keys=True,
        )
        session_key = "analytics:last_search_signature"
        if self.request.session.get(session_key) == signature:
            return

        self.request.session[session_key] = signature
        AnalyticsEvent.record_event(
            event_type=AnalyticsEvent.EventType.SEARCH,
            request=self.request,
            subscriber_id=self.request.session.get("subscriber_id"),
            source="search_page",
            metadata={
                "query": query,
                "selected_year": selected_year,
                "selected_tags": selected_tags,
                "result_count": result_count,
                "is_browse_mode": is_browse_mode,
            },
        )
