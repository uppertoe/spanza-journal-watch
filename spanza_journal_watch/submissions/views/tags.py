"""Topic (tag) pages, curated collections and the tag autocomplete endpoint."""

import json

from django.core.cache import cache
from django.db.models import Count, Exists, F, IntegerField, OuterRef, Prefetch, Subquery, Value, Window
from django.db.models.functions import Coalesce, RowNumber
from django.http import JsonResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.decorators.cache import cache_page
from django.views.generic import DetailView, ListView
from view_breadcrumbs import BaseBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.backend.models import (
    PubmedArticle,
)
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.mixins import AnonymousCacheMixin, HtmxMixin, SidebarMixin

from ..models import CuratedCollection, Review, Tag
from ..templatetags.tag_scores import compute_tag_scores
from .shared import attach_review_display_fields, build_request_absolute_url


def _curated_tag_queryset_with_review_count():
    active_review_counts = (
        Review.objects.filter(active=True, article__tags__id=OuterRef("pk"))
        .values("article__tags__id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    return Tag.objects.filter(active=True, curated=True).annotate(
        review_count=Coalesce(
            Subquery(active_review_counts, output_field=IntegerField()),
            Value(0),
        )
    )


class TagListView(AnonymousCacheMixin, SidebarMixin, HtmxMixin, ListBreadcrumbMixin, ListView):
    model = Tag
    context_object_name = "tags"
    template_name = "submissions/tag_list.html"
    queryset = Tag.objects.exclude(active=False)

    # Breadcrumb
    breadcrumb_use_pk = False

    @cached_property
    def crumbs(self):
        return [("Explore", "")]

    # HTMX
    htmx_templates = ["submissions/fragments/tag_results.html"]

    # Frontend options
    paginate_by = None
    issue_cols = 4

    def get_queryset(self):
        query = (self.request.GET.get("q") or "").strip()
        sort = (self.request.GET.get("sort") or "popular").strip()

        queryset = _curated_tag_queryset_with_review_count()

        # Hide empty tags to keep the list meaningful
        queryset = queryset.filter(review_count__gt=0)

        if query:
            normalized_query = query[1:] if query.startswith("#") else query
            queryset = queryset.filter(text__icontains=normalized_query)

        if sort == "name":
            return queryset.order_by("text")

        if sort == "trending":
            # Sort by engagement score client-side after annotation
            tag_scores = compute_tag_scores()
            tags = list(queryset.order_by("-review_count", "text"))
            tags.sort(key=lambda t: tag_scores.get(t.id, {}).get("score", 0), reverse=True)
            return tags

        return queryset.order_by("-review_count", "text")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue_cols"] = self.issue_cols
        context["query"] = (self.request.GET.get("q") or "").strip()
        context["sort"] = (self.request.GET.get("sort") or "popular").strip()
        context["result_count"] = context["paginator"].count if context["paginator"] else len(context["tags"])

        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["filter_querystring"] = query_params.urlencode()
        context["page_title"] = "Explore topics | SPANZA Journal Watch"
        context["page_meta_description"] = (
            "Explore SPANZA Journal Watch reviews by topic: browse the themes and subject areas "
            "covered across our paediatric anaesthesia literature reviews."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:tag_list"))
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Explore topics",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )
        if context["query"] or context["sort"] != "popular" or self.request.GET.get("page"):
            context["meta_robots"] = "noindex,follow"

        page_obj = context.get("page_obj")
        if page_obj:
            total_pages = page_obj.paginator.num_pages
            current_page = page_obj.number
            window = 2

            page_links = [1]
            start = max(2, current_page - window)
            end = min(total_pages - 1, current_page + window)

            if start > 2:
                page_links.append(None)

            page_links.extend(range(start, end + 1))

            if end < total_pages - 1:
                page_links.append(None)

            if total_pages > 1:
                page_links.append(total_pages)

            context["page_links"] = page_links
            context["total_pages"] = total_pages

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {}
        context["page_header"] = header.collate_fields(**override) if header else override

        # --- Explore: featured topics (first page, no search) ---
        is_first_page = not self.request.GET.get("page") or self.request.GET.get("page") == "1"
        show_explore = is_first_page and not context["query"]
        context["show_explore"] = show_explore

        if show_explore:
            tag_scores = compute_tag_scores()
            # Build featured tags: top 12 curated tags by engagement score
            scored_tags = _curated_tag_queryset_with_review_count().filter(
                id__in=tag_scores.keys(), review_count__gt=0
            )
            featured = []
            for tag in scored_tags:
                tag.engagement_score = tag_scores[tag.id]["score"]
                featured.append(tag)
            featured.sort(key=lambda t: t.engagement_score, reverse=True)
            featured = featured[:12]

            # Assign heat tier for visual indicator
            for i, tag in enumerate(featured):
                if i < 4:
                    tag.heat_tier = "hot"
                elif i < 8:
                    tag.heat_tier = "warm"
                else:
                    tag.heat_tier = "mild"

            # Batch-attach top 2 reviews per featured tag (1 query instead of 12)
            featured_tag_ids = [t.pk for t in featured]
            tag_review_links = (
                Review.objects.filter(active=True, article__tags__id__in=featured_tag_ids)
                .annotate(
                    featured_tag_id=F("article__tags__id"),
                    featured_tag_rank=Window(
                        expression=RowNumber(),
                        partition_by=[F("article__tags__id")],
                        order_by=[F("publish_date").desc(nulls_last=True), F("pk").desc()],
                    ),
                )
                .filter(featured_tag_rank__lte=2)
                .values_list("featured_tag_id", "pk")
            )
            # Collect review PKs per tag, limited to 2 each
            tag_review_pks: dict[int, list[int]] = {}
            for tag_id, review_pk in tag_review_links:
                pks = tag_review_pks.setdefault(tag_id, [])
                if len(pks) < 2 and review_pk not in pks:
                    pks.append(review_pk)
            all_review_pks = {pk for pks in tag_review_pks.values() for pk in pks}
            review_map = (
                {r.pk: r for r in Review.objects.filter(pk__in=all_review_pks).select_related("article")}
                if all_review_pks
                else {}
            )
            for tag in featured:
                tag.top_reviews = [review_map[pk] for pk in tag_review_pks.get(tag.pk, []) if pk in review_map]

            # Group featured tags by cluster if available
            clusters = cache.get("tag_clusters", [])
            if clusters:
                tag_to_cluster = {}
                for i, cluster_ids in enumerate(clusters):
                    for tid in cluster_ids:
                        tag_to_cluster[tid] = i
                for tag in featured:
                    tag.cluster_index = tag_to_cluster.get(tag.id)

            context["featured_tags"] = featured
            context["collections"] = CuratedCollection.objects.filter(active=True).prefetch_related("tags")

        return context


class TagDetailView(AnonymousCacheMixin, SidebarMixin, BaseBreadcrumbMixin, DetailView):
    model = Tag
    context_object_name = "tag"
    template_name = "submissions/tag_detail.html"
    queryset = Tag.objects.exclude(active=False).prefetch_related(
        Prefetch(
            "articles",
            queryset=(
                PubmedArticle.objects.select_related("journal").prefetch_related(
                    Prefetch(
                        "reviews",
                        queryset=Review.objects.filter(active=True)
                        .select_related("author", "article__journal")
                        .prefetch_related("article__tags", "issues")
                        .order_by("-created"),
                    )
                )
            ),
        )
    )

    # Breadcrumb
    @cached_property
    def crumbs(self):
        return [("Explore", reverse("submissions:tag_list")), (self.object, "")]

    # Frontend options
    article_cols = 1

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {"title": str(self.object)}
        context["page_header"] = header.collate_fields(**override) if header else override

        context["article_cols"] = self.article_cols
        context["page_title"] = f"{self.object} | SPANZA Journal Watch"
        tag_reviews = []
        for article in self.object.articles.all():
            latest_review = next(iter(article.reviews.all()), None)
            if latest_review is not None:
                tag_reviews.append(latest_review)
        attach_review_display_fields(tag_reviews)
        context["tag_reviews"] = tag_reviews

        review_count = len(tag_reviews)
        count_label = f"{review_count} SPANZA Journal Watch review{'s' if review_count != 1 else ''}"
        context["page_meta_description"] = (
            f"{count_label} on {self.object}: summaries and expert commentary on "
            "paediatric anaesthesia research, selected from the literature by SPANZA members."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, self.object.get_absolute_url())
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": str(self.object),
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )

        return context


class CuratedCollectionDetailView(AnonymousCacheMixin, SidebarMixin, BaseBreadcrumbMixin, DetailView):
    model = CuratedCollection
    context_object_name = "collection"
    template_name = "submissions/collection_detail.html"
    queryset = CuratedCollection.objects.filter(active=True).prefetch_related("tags")
    article_cols = 1

    @cached_property
    def crumbs(self):
        return [("Explore", reverse("submissions:tag_list")), (self.object, "")]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collection = self.object

        # Prefer manually curated reviews; fall back to tag-based reviews
        if collection.reviews.exists():
            reviews = list(
                collection.reviews.filter(active=True)
                .select_related("author", "article__journal")
                .prefetch_related("article__tags", "issues")
                .order_by("-publish_date")
            )
        else:
            tag_ids = list(collection.tags.values_list("id", flat=True))
            if tag_ids:
                TagArticle = Tag.articles.through
                reviews = list(
                    Review.objects.filter(active=True)
                    .filter(
                        Exists(TagArticle.objects.filter(pubmedarticle_id=OuterRef("article_id"), tag_id__in=tag_ids))
                    )
                    .select_related("author", "article__journal")
                    .prefetch_related("article__tags", "issues")
                    .order_by("-publish_date")
                )
            else:
                reviews = []
        attach_review_display_fields(reviews)
        context["tag_reviews"] = reviews
        context["article_cols"] = self.article_cols
        context["page_title"] = f"{collection.title} | SPANZA Journal Watch"
        context["page_meta_description"] = collection.description or (
            f"{collection.title}: a curated collection of SPANZA Journal Watch reviews and "
            "expert commentary on paediatric anaesthesia research."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, collection.get_absolute_url())

        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {"title": collection.title}
        if collection.description:
            override["body"] = collection.description
        context["page_header"] = header.collate_fields(**override) if header else override

        return context


@cache_page(3600)
def ajax_get_tags(request):
    tags_queryset = (
        Tag.objects.filter(active=True, curated=True)
        .annotate(article_count=Count("articles"))
        .order_by("-article_count")
        .values_list("text", flat=True)
    )
    tags_list = [f"#{t}" for t in tags_queryset]
    data = {"tags": tags_list}
    return JsonResponse(data)
