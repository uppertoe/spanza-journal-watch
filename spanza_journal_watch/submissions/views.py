import datetime
import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import BaseBreadcrumbMixin, DetailBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.analytics.models import AnalyticsEvent, PageView
from spanza_journal_watch.backend.models import (
    PubmedArticle,
    PubmedArticleUserState,
    WatchedJournal,
    WatchedJournalArticle,
    can_recommend_pubmed_articles,
)
from spanza_journal_watch.backend.pubmed_cache import article_metadata_list, shift_month
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.functions import get_domain_url, shorten_text
from spanza_journal_watch.utils.mixins import HitMixin, HtmxMixin, SidebarMixin

from .models import Author, CuratedCollection, HealthService, Issue, Review, Tag
from .templatetags.tag_scores import compute_tag_scores

# ---------------------------------------------------------------------------
# Journal browser: section grouping for table-of-contents
# ---------------------------------------------------------------------------

JOURNAL_SECTIONS = [
    ("Editorials", "editorials", {"Editorial", "Comment", "Published Erratum", "Introductory Journal Article"}),
    (
        "Reviews",
        "reviews",
        {"Review", "Systematic Review", "Meta-Analysis", "Scoping Review", "Network Meta-Analysis"},
    ),
    ("Guidelines", "guidelines", {"Practice Guideline", "Consensus Statement"}),
    (
        "Original Research",
        "original-research",
        {
            "Randomized Controlled Trial",
            "Clinical Trial",
            "Clinical Trial, Phase I",
            "Clinical Trial, Phase II",
            "Clinical Trial, Phase III",
            "Clinical Trial, Phase IV",
            "Observational Study",
            "Multicenter Study",
            "Comparative Study",
            "Equivalence Trial",
            "Pragmatic Clinical Trial",
            "Validation Study",
        },
    ),
    ("Case Reports", "case-reports", {"Case Reports"}),
    ("Letters", "letters", {"Letter"}),
]

IGNORED_PUBLICATION_TYPES = {
    "Journal Article",
    "Research Support, Non-U.S. Gov't",
    "Research Support, U.S. Gov't, P.H.S.",
    "Research Support, U.S. Gov't, Non-P.H.S.",
    "Research Support, N.I.H., Extramural",
    "Research Support, N.I.H., Intramural",
    "Video-Audio Media",
    "Historical Article",
    "Lecture",
}


def _group_articles_by_section(rows):
    """Group article rows into (section_name, section_slug, [rows]) tuples.

    Each article is placed in the first matching section based on its
    publication_types, with title-based heuristics as a fallback for articles
    that PubMed only tags as "Journal Article".  Empty sections are omitted.
    """
    import re

    # Title patterns that indicate correspondence (Comment/Reply at end of title)
    _correspondence_re = re.compile(r":\s*(Comment|Reply|Response|Correspondence|Authors?\s*Reply)\.?\s*$", re.I)

    buckets = {slug: [] for _, slug, _ in JOURNAL_SECTIONS}
    buckets["articles"] = []  # fallback

    for row in rows:
        ptypes = set(row.publication_types) - IGNORED_PUBLICATION_TYPES
        placed = False
        for _name, slug, type_set in JOURNAL_SECTIONS:
            if ptypes & type_set:
                buckets[slug].append(row)
                placed = True
                break
        if not placed:
            # Title-based heuristic: articles ending with ": Comment." etc. go to Letters
            title = row.article.title or ""
            if _correspondence_re.search(title):
                buckets["letters"].append(row)
                placed = True
        if not placed:
            buckets["articles"].append(row)

    sections = []
    for name, slug, _ in JOURNAL_SECTIONS:
        if buckets[slug]:
            sections.append((name, slug, buckets[slug]))
    if buckets["articles"]:
        sections.append(("Articles", "articles", buckets["articles"]))
    return sections


def build_share_urls(
    share_title,
    canonical_url,
    share_description="",
    *,
    journal_name="",
    email_summary="",
):
    share_text = "\n".join(part for part in [share_title, "", canonical_url] if part)
    trimmed_email_summary = shorten_text(email_summary or share_description, 900).strip()
    email_lines = [
        "This Journal Watch review is being shared with you from SPANZA Journal Watch.",
        "",
        f"Review: {share_title.removeprefix('SPANZA Journal Watch - ').strip()}",
    ]
    if journal_name:
        email_lines.append(f"Journal: {journal_name}")
    if trimmed_email_summary:
        email_lines.extend(
            [
                "",
                "A brief summary is below. You can read the full review at the link.",
                "",
                trimmed_email_summary,
            ]
        )
    email_lines.extend(["", "Read the review:", canonical_url])
    email_body = "\n".join(email_lines)
    return {
        "share_text": share_text,
        "bluesky_share_url": f"https://bsky.app/intent/compose?{urlencode({'text': share_text})}",
        "x_share_url": f"https://twitter.com/intent/tweet?{urlencode({'text': share_title, 'url': canonical_url})}",
        "facebook_share_url": f"https://www.facebook.com/sharer/sharer.php?{urlencode({'u': canonical_url})}",
        "email_share_url": f"mailto:?{urlencode({'subject': share_title, 'body': email_body})}",
    }


def build_absolute_url(path):
    return f"{get_domain_url()}{path}"


def build_request_absolute_url(request, path):
    if request is None:
        return build_absolute_url(path)
    return request.build_absolute_uri(path)


def attach_review_display_fields(reviews, *, issue=None, include_share_context=False, request=None):
    review_list = list(reviews)
    if not review_list:
        return review_list

    for review in review_list:
        review.display_review_date = issue.date if issue and issue.date else review.get_review_date()

        if include_share_context:
            article_title = review.article.get_title().strip()
            review_share_title = f"SPANZA Journal Watch - {article_title}"
            review_canonical_url = build_request_absolute_url(request, review.get_absolute_url())
            review_share_description = review.get_truncated_body().strip()
            review_share_email_summary = review.get_plain_body().strip()
            review_share_context = build_share_urls(
                review_share_title,
                review_canonical_url,
                review_share_description,
                journal_name=str(review.article.journal),
                email_summary=review_share_email_summary,
            )

            review.share_title = review_share_title
            review.canonical_url = review_canonical_url
            review.share_description = review_share_description
            review.share_email_summary = review_share_email_summary
            review.share_text = review_share_context["share_text"]
            review.bluesky_share_url = review_share_context["bluesky_share_url"]
            review.x_share_url = review_share_context["x_share_url"]
            review.facebook_share_url = review_share_context["facebook_share_url"]
            review.email_share_url = review_share_context["email_share_url"]

    # Attach star state + star counts per review for star buttons
    if request:
        article_ids = [r.article_id for r in review_list]
        # Star counts per article
        star_count_map = {}
        if article_ids:
            star_counts = (
                PubmedArticleUserState.objects.filter(
                    article_id__in=article_ids,
                    starred_at__isnull=False,
                )
                .values("article_id")
                .annotate(count=Count("id"))
            )
            star_count_map = {row["article_id"]: row["count"] for row in star_counts}

        if request.user.is_authenticated:
            state_map = {
                s.article_id: s
                for s in PubmedArticleUserState.objects.filter(user=request.user, article_id__in=article_ids)
            }
            for review in review_list:
                review.pubmed_user_state = state_map.get(review.article_id)
                review.pubmed_session_starred = False
                review.star_target_id = f"review-star-actions-{review.pk}"
                review.star_count = star_count_map.get(review.article_id, 0)
        else:
            starred_ids = set(request.session.get("starred_article_ids", []))
            for review in review_list:
                review.pubmed_user_state = None
                review.pubmed_session_starred = review.article_id in starred_ids
                review.star_target_id = f"review-star-actions-{review.pk}"
                review.star_count = star_count_map.get(review.article_id, 0)

    return review_list


class ReviewDetailView(HitMixin, SidebarMixin, HtmxMixin, BaseBreadcrumbMixin, DetailView):
    model = Review
    context_object_name = "review"
    template_name = "submissions/review_detail.html"

    # Breadcrumb
    @cached_property
    def crumbs(self):
        issue = Issue.objects.filter(reviews=self.object).latest("created")

        return [("Issues", reverse("submissions:issue_list")), (issue, issue.get_absolute_url()), (self.object, "")]

    # HTMX
    htmx_templates = ["layout/fragments/card_modal.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        canonical_url = build_request_absolute_url(self.request, self.object.get_absolute_url())

        article_title = self.object.article.get_title().strip()
        share_title = f"SPANZA Journal Watch - {article_title}"
        share_description = self.object.get_truncated_body().strip()
        share_email_summary = self.object.get_plain_body().strip()
        attach_review_display_fields([self.object], request=self.request)
        review_date = self.object.display_review_date
        share_context = build_share_urls(
            share_title,
            canonical_url,
            share_description,
            journal_name=str(self.object.article.journal),
            email_summary=share_email_summary,
        )

        context["canonical_url"] = canonical_url
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": share_title,
                "description": share_description,
                "url": canonical_url,
                "datePublished": review_date.isoformat() if review_date else "",
                "author": {
                    "@type": "Person",
                    "name": str(self.object.author),
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Journal Watch",
                },
            }
        )
        context["share_title"] = share_title
        context["share_description"] = share_description
        context["share_email_summary"] = share_email_summary
        context["share_text"] = share_context["share_text"]
        context["share_image_url"] = (
            self.request.build_absolute_uri(self.object.feature_image.url) if self.object.feature_image else ""
        )
        context["bluesky_share_url"] = share_context["bluesky_share_url"]
        context["x_share_url"] = share_context["x_share_url"]
        context["facebook_share_url"] = share_context["facebook_share_url"]
        context["email_share_url"] = share_context["email_share_url"]

        # Override header
        override = {"title": self.object.get_full_name()}
        header = PageHeader.get_active_for(PageHeader.PageType.REVIEW_DETAIL)
        context["page_header"] = header.collate_fields(**override) if header else override

        # Related reviews
        pubmed_article = self.object.article
        context["related_reviews"] = pubmed_article.get_related_reviews(limit=4)

        # Star button context — review.article IS the PubmedArticle after merge
        context["pubmed_article"] = pubmed_article
        context["star_count"] = PubmedArticleUserState.objects.filter(
            article=pubmed_article, starred_at__isnull=False
        ).count()
        if self.request.user.is_authenticated:
            context["pubmed_user_state"] = PubmedArticleUserState.objects.filter(
                user=self.request.user, article=pubmed_article
            ).first()
        else:
            context["pubmed_session_starred"] = pubmed_article.pk in self.request.session.get(
                "starred_article_ids", []
            )

        return context


class IssueDetailView(HitMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, DetailBreadcrumbMixin, ListView):
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
        self.object = self.get_object(queryset=Issue.objects.exclude(active=False))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue"] = self.object
        context["article_cols"] = self.article_cols
        context["page_title"] = f"{self.object.name} | SPANZA Journal Watch"
        context["page_meta_description"] = (
            f"{self.object.name}: curated Journal Watch reviews and commentary "
            "from the paediatric anaesthesia literature."
        )
        context["canonical_url"] = self.request.build_absolute_uri()
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": self.object.name,
                "url": build_request_absolute_url(self.request, self.object.get_absolute_url()),
                "description": context["page_meta_description"],
            }
        )

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

        # Attach related reviews to each review on this page
        for review in context["articles"]:
            review.related_reviews = review.article.get_related_reviews()

        return context

    def get_queryset(self):
        return (
            Review.objects.filter(issues=self.object, active=True)
            .order_by("-created")
            .select_related("article__journal", "author")
            .prefetch_related("article__tags", "issues")
        )


class IssueListView(SidebarMixin, HtmxMixin, ListBreadcrumbMixin, ListView):
    model = Issue
    context_object_name = "issues"
    template_name = "submissions/issue_list.html"
    queryset = Issue.objects.exclude(active=False).order_by("-date")

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
            "Browse previous SPANZA Journal Watch issues and collections of paediatric anaesthesia literature reviews."
        )
        context["canonical_url"] = self.request.build_absolute_uri()
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


class TagListView(SidebarMixin, HtmxMixin, ListBreadcrumbMixin, ListView):
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

        queryset = Tag.objects.filter(active=True, curated=True).annotate(
            review_count=Count("articles__reviews", filter=Q(articles__reviews__active=True), distinct=True)
        )

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
        context["page_meta_description"] = "Browse topics and themes used across SPANZA Journal Watch reviews."
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
            scored_tags = (
                Tag.objects.filter(active=True, curated=True, id__in=tag_scores.keys())
                .annotate(
                    review_count=Count("articles__reviews", filter=Q(articles__reviews__active=True), distinct=True)
                )
                .filter(review_count__gt=0)
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

            # Attach top 2 review titles per featured tag
            for tag in featured:
                top_reviews = list(
                    Review.objects.filter(active=True, article__tags=tag)
                    .select_related("article__journal")
                    .order_by("-publish_date")[:2]
                )
                tag.top_reviews = top_reviews

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


class TagDetailView(SidebarMixin, BaseBreadcrumbMixin, DetailView):
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
        # Record page view
        obj = super().get_object(queryset)
        subscriber_id = self.request.session.get("subscriber_id")
        PageView.record_view(obj, subscriber_id, request=self.request)
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {"title": str(self.object)}
        context["page_header"] = header.collate_fields(**override) if header else override

        context["article_cols"] = self.article_cols
        context["page_title"] = f"{self.object} | SPANZA Journal Watch"
        context["page_meta_description"] = f"Browse Journal Watch reviews tagged {self.object}."
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

        tag_reviews = []
        for article in self.object.articles.all():
            latest_review = next(iter(article.reviews.all()), None)
            if latest_review is not None:
                tag_reviews.append(latest_review)
        attach_review_display_fields(tag_reviews)
        context["tag_reviews"] = tag_reviews

        return context


class CuratedCollectionDetailView(SidebarMixin, BaseBreadcrumbMixin, DetailView):
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
            reviews = (
                list(
                    Review.objects.filter(active=True, article__tags__id__in=tag_ids)
                    .select_related("author", "article__journal")
                    .prefetch_related("article__tags", "issues")
                    .distinct()
                    .order_by("-publish_date")
                )
                if tag_ids
                else []
            )
        attach_review_display_fields(reviews)
        context["tag_reviews"] = reviews
        context["article_cols"] = self.article_cols
        context["page_title"] = f"{collection.title} | SPANZA Journal Watch"
        context["page_meta_description"] = (
            collection.description or f"A curated collection of reviews: {collection.title}."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, collection.get_absolute_url())

        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {"title": collection.title}
        if collection.description:
            override["body"] = collection.description
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


class SearchView(BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, TemplateView):
    template_name = "submissions/search.html"

    # HTMX
    htmx_templates = ["submissions/fragments/search_results.html"]

    # Search settings
    sim_thres = 0.1
    no_result_message = "No results found"

    # Breadcrumb
    @cached_property
    def crumbs(self):
        return [("Search", reverse("submissions:search"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("q") or "").strip()
        selected_year = (self.request.GET.get("year") or "").strip()
        selected_tags = [slug for slug in self.request.GET.getlist("tag") if slug]

        # Accept comma-separated tags as a fallback (useful for manually edited URLs)
        if not selected_tags:
            comma_tags = (self.request.GET.get("tags") or "").strip()
            if comma_tags:
                selected_tags = [slug.strip() for slug in comma_tags.split(",") if slug.strip()]

        search_results = self.search(query, year=selected_year, tag_slugs=selected_tags)
        context.update(search_results)

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

        self.record_search_event(
            query=query,
            selected_year=selected_year,
            selected_tags=selected_tags,
            result_count=search_results["result_count"],
            is_browse_mode=search_results["is_browse_mode"],
        )

        return context

    def record_search_event(self, *, query, selected_year, selected_tags, result_count, is_browse_mode):
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

    def search(self, query, year="", tag_slugs=None):
        tag_slugs = tag_slugs or []

        if query:
            reviews = Review.search(query)
        else:
            reviews = (
                Review.objects.exclude(active=False)
                .select_related("article__journal", "author")
                .prefetch_related("article__tags", "issues")
                .order_by("-created")
            )

        if year and str(year).isdigit():
            reviews = reviews.filter(publish_date__year=int(year))

        if tag_slugs:
            reviews = reviews.filter(article__tags__slug__in=tag_slugs).distinct()

        reviews = reviews.prefetch_related("article__tags", "issues")
        result_count = reviews.count()

        # Keep search pages fast and readable
        result_reviews = list(reviews[:80])
        attach_review_display_fields(result_reviews)
        results = {
            "result_reviews": result_reviews,
            "result_count": result_count,
            "is_browse_mode": not bool(query),
        }

        if result_count == 0:
            # Add a message if no results
            results["no_result_message"] = self.no_result_message

        return results


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


class AuthorDetailView(HitMixin, BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, ListView):
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
        context["page_meta_description"] = f"Reviews contributed to SPANZA Journal Watch by {self.object}."
        context["canonical_url"] = build_request_absolute_url(self.request, self.object.get_absolute_url())
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


class HealthServiceListView(BaseBreadcrumbMixin, SidebarMixin, HtmxMixin, ListView):
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
        return context


def _parse_journal_month(value):
    text = (value or "").strip()
    if not text:
        return timezone.now().date().replace(day=1)
    try:
        return datetime.datetime.strptime(text, "%Y-%m").date().replace(day=1)
    except ValueError:
        return timezone.now().date().replace(day=1)


def _best_default_month(journal_id, min_articles=10):
    """Find the most recent month with at least `min_articles` for this journal.

    Falls back to the most recent month with any articles, or the current month.
    """
    from django.db.models import Count as _Count

    months = (
        WatchedJournalArticle.objects.filter(watched_journal_id=journal_id)
        .values("publication_month")
        .annotate(article_count=_Count("id"))
        .order_by("-publication_month")[:12]
    )
    fallback = None
    for row in months:
        if fallback is None:
            fallback = row["publication_month"]
        if row["article_count"] >= min_articles:
            return row["publication_month"]
    return fallback or timezone.now().date().replace(day=1)


def _journal_month_options():
    months = list(
        WatchedJournalArticle.objects.exclude(publication_month__isnull=True)
        .values_list("publication_month", flat=True)
        .distinct()
        .order_by("-publication_month")[:24]
    )
    current_month = timezone.now().date().replace(day=1)
    if current_month not in months:
        months.insert(0, current_month)
    return months


def _journal_browser_context(request):
    """Build context for the single-journal browsable view."""
    active_journals = list(WatchedJournal.objects.filter(active=True, visible_on_frontend=True).order_by("name", "pk"))
    shelf_tones = ["cobalt", "sunset", "sage", "berry", "ochre", "marine", "rose", "slate"]
    for journal in active_journals:
        journal.shelf_tone = shelf_tones[journal.pk % len(shelf_tones)]

    # --- Determine selected journal (single, not multi) ---
    raw_journal = request.GET.get("journal")
    selected_journal_id = int(raw_journal) if raw_journal and str(raw_journal).isdigit() else None

    if selected_journal_id is None:
        if request.user.is_authenticated and getattr(request.user, "last_viewed_journal_id", None):
            selected_journal_id = request.user.last_viewed_journal_id
        else:
            selected_journal_id = request.session.get("last_viewed_journal_id")

    # Validate the ID exists among active journals
    active_ids = {j.pk for j in active_journals}
    if selected_journal_id not in active_ids:
        selected_journal_id = active_journals[0].pk if active_journals else None

    selected_journal = next((j for j in active_journals if j.pk == selected_journal_id), None)

    # Persist last-viewed
    if selected_journal_id:
        request.session["last_viewed_journal_id"] = selected_journal_id
        if request.user.is_authenticated and request.user.last_viewed_journal_id != selected_journal_id:
            from django.contrib.auth import get_user_model

            get_user_model().objects.filter(pk=request.user.pk).update(last_viewed_journal_id=selected_journal_id)

    # --- Month selection + prev/next ---
    raw_month = request.GET.get("month")
    if raw_month:
        selected_month = _parse_journal_month(raw_month)
    else:
        # Find the most recent month with a reasonable number of articles for this journal
        selected_month = _best_default_month(selected_journal_id)
    prev_month = shift_month(selected_month, -1)
    next_month = shift_month(selected_month, 1)
    has_prev_month = WatchedJournalArticle.objects.filter(
        watched_journal_id=selected_journal_id, publication_month=prev_month
    ).exists()
    has_next_month = WatchedJournalArticle.objects.filter(
        watched_journal_id=selected_journal_id, publication_month=next_month
    ).exists()

    # --- Fetch articles for this journal + month ---
    article_links = (
        WatchedJournalArticle.objects.filter(
            publication_month=selected_month,
            watched_journal_id=selected_journal_id,
        )
        .select_related("article", "watched_journal")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            ),
            star_count=Count(
                "article__user_states",
                filter=Q(article__user_states__starred_at__isnull=False),
                distinct=True,
            ),
        )
        .order_by("-article__publication_date", "-article__publication_month", "article__title")
    )

    article_links = list(article_links)
    user_state_map = {}
    if request.user.is_authenticated:
        user_state_map = {
            state.article_id: state
            for state in PubmedArticleUserState.objects.filter(
                user=request.user, article_id__in=[link.article_id for link in article_links]
            )
        }

    session_starred_ids = set()
    if not request.user.is_authenticated:
        session_starred_ids = set(request.session.get("starred_article_ids", []))

    # Build PubmedArticle.pk → Review lookup for articles that have been reviewed
    pubmed_ids = [link.article_id for link in article_links]
    review_map = {}
    if pubmed_ids:
        reviewed = Review.objects.filter(active=True, article_id__in=pubmed_ids).select_related("author")
        for rev in reviewed:
            review_map.setdefault(rev.article_id, rev)

    rows = []
    seen_article_ids = set()
    for link in article_links:
        if link.article_id in seen_article_ids:
            continue
        seen_article_ids.add(link.article_id)
        link.user_state = user_state_map.get(link.article_id)
        link.session_starred = link.article_id in session_starred_ids
        link.publication_types = article_metadata_list(link.article, "publication_types")
        link.mesh_terms = article_metadata_list(link.article, "mesh_terms")
        link.keywords = article_metadata_list(link.article, "keywords")
        link.review = review_map.get(link.article_id)
        rows.append(link)

    sections = _group_articles_by_section(rows)
    _attach_related_reviews(rows)

    return {
        "rows": rows,
        "sections": sections,
        "active_journals": active_journals,
        "selected_journal_id": selected_journal_id,
        "selected_journal": selected_journal,
        "selected_month": selected_month,
        "prev_month": prev_month,
        "next_month": next_month,
        "has_prev_month": has_prev_month,
        "has_next_month": has_next_month,
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "session_starred_ids": session_starred_ids,
    }


def _attach_related_reviews(rows):
    """Batch-attach related reviews to all journal browser articles based on shared curated tags."""
    if not rows:
        return

    article_ids = [r.article_id for r in rows]

    # Fetch curated tag IDs per article in one query
    tag_links = Tag.objects.filter(curated=True, active=True, articles__id__in=article_ids).values_list(
        "articles__id", "id"
    )
    article_tag_map = {}
    for article_id, tag_id in tag_links:
        article_tag_map.setdefault(article_id, set()).add(tag_id)

    for row in rows:
        tag_ids = article_tag_map.get(row.article_id, set())
        if not tag_ids:
            row.related_reviews = []
            continue
        related = (
            Review.objects.filter(active=True, article__tags__id__in=tag_ids)
            .exclude(article_id=row.article_id)
            .select_related("article__journal", "author")
            .annotate(shared_tag_count=Count("article__tags", filter=Q(article__tags__id__in=tag_ids)))
            .filter(shared_tag_count__gte=1)
            .order_by("-shared_tag_count", "-publish_date")
            .distinct()[:2]
        )
        row.related_reviews = list(related)


def _journal_article_actions_context(request, article):
    user_state = None
    session_starred = False
    if request.user.is_authenticated:
        user_state = PubmedArticleUserState.objects.filter(user=request.user, article=article).first()
    else:
        session_starred = article.pk in request.session.get("starred_article_ids", [])
    star_count = PubmedArticleUserState.objects.filter(article=article, starred_at__isnull=False).count()
    review = Review.objects.filter(active=True, article=article).select_related("author").first()
    return {
        "article": article,
        "user_state": user_state,
        "session_starred": session_starred,
        "star_count": star_count,
        "review": review,
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "next_url": request.POST.get("next") or request.GET.get("next") or request.get_full_path(),
    }


class JournalListView(TemplateView):
    template_name = "submissions/journal_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_journal_browser_context(self.request))
        context["page_title"] = "Journals | Journal Watch"
        context["page_meta_description"] = (
            "Browse cached PubMed articles from watched journals by month, with filters and community recommendations."
        )
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:journal_list"))
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Journal browser",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("HX-Request") == "true":
            context["is_htmx"] = True
            return render(self.request, "submissions/fragments/journal_article_list.html", context, **response_kwargs)
        return super().render_to_response(context, **response_kwargs)


@require_POST
def journal_article_toggle_star(request, article_id):
    article = get_object_or_404(PubmedArticle, pk=article_id)

    if request.user.is_authenticated:
        state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
        state.starred_at = None if state.starred_at else timezone.now()
        state.save(update_fields=["starred_at", "modified"])
    else:
        starred = request.session.get("starred_article_ids", [])
        if article.pk in starred:
            starred.remove(article.pk)
        else:
            starred.append(article.pk)
        request.session["starred_article_ids"] = starred

    if request.headers.get("HX-Request") == "true":
        source = request.POST.get("source", "")
        triggers = {}

        if source == "reading_list":
            # Return empty response to remove the card via outerHTML swap
            response = HttpResponse("")
        elif source == "review":
            # Return the review star button fragment
            star_target_id = request.POST.get("star_target_id", "")
            star_count = PubmedArticleUserState.objects.filter(article=article, starred_at__isnull=False).count()
            ctx = {"pubmed_article": article, "star_count": star_count}
            if request.POST.get("hide_star_count"):
                ctx["hide_star_count"] = True
            if star_target_id:
                ctx["star_target_id"] = star_target_id
            if request.user.is_authenticated:
                ctx["pubmed_user_state"] = PubmedArticleUserState.objects.filter(
                    user=request.user, article=article
                ).first()
            else:
                ctx["pubmed_session_starred"] = article.pk in request.session.get("starred_article_ids", [])
            response = render(request, "submissions/fragments/review_star_button.html", ctx)
        else:
            response = render(
                request,
                "submissions/fragments/journal_article_actions.html",
                _journal_article_actions_context(request, article),
            )

        if not request.user.is_authenticated:
            starred_count = len(request.session.get("starred_article_ids", []))
            triggers["showLoginPrompt"] = {"count": starred_count}

        # Notify reading list dot indicator
        triggers["starChanged"] = True
        if triggers:
            response["HX-Trigger"] = json.dumps(triggers)

        return response
    return redirect(request.POST.get("next") or reverse("submissions:journal_list"))


@csrf_exempt
@require_POST
def journal_article_mark_fulltext(request, article_id):
    """Record that a user clicked full text. Works for both authenticated and anonymous users."""
    try:
        article = PubmedArticle.objects.get(pk=article_id)
    except PubmedArticle.DoesNotExist:
        return JsonResponse({"ok": False}, status=404)

    if request.user.is_authenticated:
        state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
        if not state.full_text_clicked_at:
            state.full_text_clicked_at = timezone.now()
            state.save(update_fields=["full_text_clicked_at"])
    else:
        clicked = request.session.get("fulltext_clicked_ids", [])
        if article.pk not in clicked:
            clicked.append(article.pk)
            request.session["fulltext_clicked_ids"] = clicked

    return JsonResponse({"ok": True})


def journal_fulltext_ids(request):
    """Return article IDs the current user/session has clicked full text on."""
    if request.user.is_authenticated:
        ids = list(
            PubmedArticleUserState.objects.filter(user=request.user, full_text_clicked_at__isnull=False).values_list(
                "article_id", flat=True
            )
        )
    else:
        ids = request.session.get("fulltext_clicked_ids", [])
    return JsonResponse({"ids": ids})


@login_required
@require_POST
def journal_article_toggle_recommend(request, article_id):
    article = get_object_or_404(PubmedArticle, pk=article_id)
    if not can_recommend_pubmed_articles(request.user):
        messages.error(request, "You do not have permission to recommend articles yet.")
        return redirect(request.POST.get("next") or reverse("submissions:journal_list"))

    state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
    state.recommended_at = None if state.recommended_at else timezone.now()
    state.save(update_fields=["recommended_at", "modified"])

    if request.headers.get("HX-Request") == "true":
        return render(
            request,
            "submissions/fragments/journal_article_actions.html",
            _journal_article_actions_context(request, article),
        )
    return redirect(request.POST.get("next") or reverse("submissions:journal_list"))


def journal_search(request):
    """Live search across all journals, rendered in the search drawer."""
    query = (request.GET.get("q") or "").strip()
    journal_filter = request.GET.get("journal") or ""
    active_journals = list(WatchedJournal.objects.filter(active=True).order_by("name"))

    results = []
    if len(query) >= 2:
        qs = (
            WatchedJournalArticle.objects.select_related("article", "watched_journal")
            .annotate(
                recommendation_count=Count(
                    "article__user_states",
                    filter=Q(article__user_states__recommended_at__isnull=False),
                    distinct=True,
                )
            )
            .filter(
                Q(article__title__icontains=query)
                | Q(article__abstract__icontains=query)
                | Q(article__doi__icontains=query)
                | Q(article__pmid__icontains=query)
            )
            .order_by("-article__publication_date", "-article__publication_month", "article__title")
        )
        if journal_filter and str(journal_filter).isdigit():
            qs = qs.filter(watched_journal_id=int(journal_filter))

        seen = set()
        for link in qs[:80]:
            if link.article_id in seen:
                continue
            seen.add(link.article_id)
            results.append(link)
            if len(results) >= 50:
                break

    context = {
        "query": query,
        "results": results,
        "active_journals": active_journals,
        "journal_filter": journal_filter,
    }
    return render(request, "submissions/fragments/journal_search_results.html", context)


def journal_reading_list(request):
    """Full-width reading list with active/archived tabs, search, and journal filter."""
    from itertools import groupby

    tab = request.GET.get("tab", "active")
    query = (request.GET.get("q") or "").strip()
    journal_filter = (request.GET.get("journal") or "").strip()

    active_count = 0
    archived_count = 0
    items = []
    journal_names = set()

    if request.user.is_authenticated:
        base_qs = PubmedArticleUserState.objects.filter(user=request.user, starred_at__isnull=False).select_related(
            "article"
        )

        active_count = base_qs.filter(read_at__isnull=True).count()
        archived_count = base_qs.filter(read_at__isnull=False).count()

        if tab == "archived":
            qs = base_qs.filter(read_at__isnull=False).order_by("-read_at")
        else:
            qs = base_qs.filter(read_at__isnull=True).order_by("-starred_at")

        if query:
            qs = qs.filter(Q(article__title__icontains=query) | Q(article__abstract__icontains=query))
        if journal_filter:
            qs = qs.filter(article__source_journal_name=journal_filter)

        for state in qs:
            date_key = state.read_at if tab == "archived" else state.starred_at
            items.append(
                {
                    "article": state.article,
                    "state": state,
                    "group_key": date_key.strftime("%B %Y") if date_key else "Unknown",
                }
            )
            if state.article.source_journal_name:
                journal_names.add(state.article.source_journal_name)

        # Also get journal names from the full unfiltered set for the dropdown
        all_names = base_qs.values_list("article__source_journal_name", flat=True).distinct().order_by()
        journal_names = sorted(n for n in all_names if n)
    else:
        starred_ids = request.session.get("starred_article_ids", [])
        if starred_ids and tab != "archived":
            articles_qs = PubmedArticle.objects.filter(pk__in=starred_ids)
            if query:
                articles_qs = articles_qs.filter(Q(title__icontains=query) | Q(abstract__icontains=query))
            if journal_filter:
                articles_qs = articles_qs.filter(source_journal_name=journal_filter)

            articles_qs = articles_qs.order_by("-publication_date", "-publication_month")
            active_count = len(starred_ids)

            for article in articles_qs:
                month = article.publication_month or article.publication_date
                items.append(
                    {
                        "article": article,
                        "state": None,
                        "group_key": month.strftime("%B %Y") if month else "Unknown date",
                    }
                )
                if article.source_journal_name:
                    journal_names.add(article.source_journal_name)
            journal_names = sorted(journal_names)
        elif starred_ids:
            active_count = len(starred_ids)

    # Build star count + review lookup for reading list items
    reading_list_pubmed_ids = [item["article"].pk for item in items]
    star_count_map = {}
    if reading_list_pubmed_ids:
        star_counts = (
            PubmedArticleUserState.objects.filter(
                article_id__in=reading_list_pubmed_ids,
                starred_at__isnull=False,
            )
            .values("article_id")
            .annotate(count=Count("id"))
        )
        star_count_map = {row["article_id"]: row["count"] for row in star_counts}
    for item in items:
        item["star_count"] = star_count_map.get(item["article"].pk, 0)

    review_map = {}
    if reading_list_pubmed_ids:
        reviewed = Review.objects.filter(active=True, article_id__in=reading_list_pubmed_ids).select_related("author")
        for rev in reviewed:
            review_map.setdefault(rev.article_id, rev)
    for item in items:
        item["review"] = review_map.get(item["article"].pk)

    grouped = []
    for key, group in groupby(items, key=lambda x: x["group_key"]):
        grouped.append((key, list(group)))

    context = {
        "grouped_items": grouped,
        "total_count": len(items),
        "active_count": active_count,
        "archived_count": archived_count,
        "tab": tab,
        "query": query,
        "journal_filter": journal_filter,
        "journal_names": journal_names,
        "is_reading_list": True,
        "can_recommend": can_recommend_pubmed_articles(request.user),
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "submissions/fragments/journal_reading_list.html", context)

    # Full-page request (direct navigation / refresh) — render inside the journal shell
    context.update(_journal_browser_context(request))
    context["reading_list_fragment"] = True
    return render(request, "submissions/journal_list.html", context)


@login_required
@require_POST
def journal_article_toggle_archive(request, article_id):
    """Toggle read_at (archive/unarchive) on a starred article."""
    state = get_object_or_404(
        PubmedArticleUserState, user=request.user, article_id=article_id, starred_at__isnull=False
    )
    state.read_at = None if state.read_at else timezone.now()
    state.save(update_fields=["read_at", "modified"])

    if request.headers.get("HX-Request") == "true":
        # After toggle the card no longer belongs on the current tab — remove it.
        # Also update the tab counts via OOB swap.
        base_qs = PubmedArticleUserState.objects.filter(user=request.user, starred_at__isnull=False)
        active_count = base_qs.filter(read_at__isnull=True).count()
        archived_count = base_qs.filter(read_at__isnull=False).count()
        oob_html = (
            f'<span class="journal-reading-list__tab-count" '
            f'id="reading-list-active-count" hx-swap-oob="innerHTML:#reading-list-active-count">'
            f"{active_count}</span>"
            f'<span class="journal-reading-list__tab-count" '
            f'id="reading-list-archived-count" hx-swap-oob="innerHTML:#reading-list-archived-count">'
            f"{archived_count}</span>"
        )
        return HttpResponse(oob_html)
    return redirect(reverse("submissions:journal_list"))
