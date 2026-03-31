import datetime
import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
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
from spanza_journal_watch.backend.pubmed_cache import article_metadata_list
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.functions import get_domain_url, shorten_text
from spanza_journal_watch.utils.mixins import HitMixin, HtmxMixin, SidebarMixin

from .models import Article, Author, HealthService, Issue, Review, Tag


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
        try:
            AnalyticsEvent.record_event(
                event_type=AnalyticsEvent.EventType.PAGE_VISIT,
                request=request,
                metadata={"page": "issue"},
            )
        except Exception:
            pass
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

    # HTMX
    htmx_templates = ["submissions/fragments/tag_results.html"]

    # Frontend options
    paginate_by = 30
    issue_cols = 4

    def get_queryset(self):
        query = (self.request.GET.get("q") or "").strip()
        sort = (self.request.GET.get("sort") or "popular").strip()

        queryset = Tag.objects.exclude(active=False).annotate(
            review_count=Count("articles__reviews", filter=Q(articles__reviews__active=True), distinct=True)
        )

        # Hide empty tags to keep the list meaningful
        queryset = queryset.filter(review_count__gt=0)

        if query:
            normalized_query = query[1:] if query.startswith("#") else query
            queryset = queryset.filter(text__icontains=normalized_query)

        if sort == "name":
            return queryset.order_by("text")

        return queryset.order_by("-review_count", "text")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue_cols"] = self.issue_cols
        context["query"] = (self.request.GET.get("q") or "").strip()
        context["sort"] = (self.request.GET.get("sort") or "popular").strip()
        context["result_count"] = context["paginator"].count

        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["filter_querystring"] = query_params.urlencode()
        context["page_title"] = "Tags | SPANZA Journal Watch"
        context["page_meta_description"] = "Browse topics and themes used across SPANZA Journal Watch reviews."
        context["canonical_url"] = build_request_absolute_url(self.request, reverse("submissions:tag_list"))
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "Tags",
                "url": context["canonical_url"],
                "description": context["page_meta_description"],
            }
        )
        if context["query"] or context["sort"] != "popular" or self.request.GET.get("page"):
            context["meta_robots"] = "noindex,follow"

        page_obj = context["page_obj"]
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

        return context


class TagDetailView(SidebarMixin, DetailBreadcrumbMixin, DetailView):
    model = Tag
    context_object_name = "tag"
    template_name = "submissions/tag_detail.html"
    queryset = Tag.objects.exclude(active=False).prefetch_related(
        Prefetch(
            "articles",
            queryset=(
                Article.objects.select_related("journal").prefetch_related(
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
    breadcrumb_use_pk = False

    # Frontend options
    article_cols = 1

    def get_object(self, queryset=None):
        # Record page view
        obj = super().get_object(queryset)
        subscriber_id = self.request.session.get("subscriber_id")
        PageView.record_view(obj, subscriber_id, request=self.request)
        try:
            AnalyticsEvent.record_event(
                event_type=AnalyticsEvent.EventType.PAGE_VISIT,
                request=self.request,
                metadata={"page": "tag"},
            )
        except Exception:
            pass
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
                Tag.objects.exclude(active=False)
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
    tags_queryset = Tag.get_all_tags()
    tags_list = [str(tag) for tag in tags_queryset]
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


def _journal_article_matches_subject(article, subject_query):
    subject_query = (subject_query or "").strip().lower()
    if not subject_query:
        return True
    metadata_values = article_metadata_list(article, "mesh_terms") + article_metadata_list(article, "keywords")
    metadata_text = " ".join(metadata_values).lower()
    return subject_query in metadata_text


def _available_publication_types(journal_links):
    publication_types = set()
    seen_article_ids = set()
    for link in journal_links:
        if link.article_id in seen_article_ids:
            continue
        seen_article_ids.add(link.article_id)
        publication_types.update(article_metadata_list(link.article, "publication_types"))
    return sorted(publication_types)


def _journal_browser_rows(request):
    active_journals = list(WatchedJournal.objects.filter(active=True).order_by("name", "pk"))
    shelf_tones = [
        "cobalt",
        "sunset",
        "sage",
        "berry",
        "ochre",
        "marine",
        "rose",
        "slate",
    ]
    for journal in active_journals:
        journal.shelf_tone = shelf_tones[journal.pk % len(shelf_tones)]

    selected_month = _parse_journal_month(request.GET.get("month"))
    selected_journal_ids = [int(value) for value in request.GET.getlist("journal") if str(value).isdigit()]
    if not selected_journal_ids and request.user.is_authenticated:
        selected_journal_ids = list(request.user.watched_journals.filter(active=True).values_list("pk", flat=True))
    if not selected_journal_ids:
        selected_journal_ids = [journal.pk for journal in active_journals]

    query = (request.GET.get("q") or "").strip()
    publication_type = (request.GET.get("publication_type") or "").strip()
    subject = (request.GET.get("subject") or "").strip()
    article_links = (
        WatchedJournalArticle.objects.filter(publication_month=selected_month)
        .select_related("article", "watched_journal")
        .annotate(
            recommendation_count=Count(
                "article__user_states",
                filter=Q(article__user_states__recommended_at__isnull=False),
                distinct=True,
            )
        )
        .order_by(
            "-article__publication_date",
            "-article__publication_month",
            "article__title",
            "watched_journal__name",
        )
    )
    if selected_journal_ids:
        article_links = article_links.filter(watched_journal_id__in=selected_journal_ids)
    if query:
        article_links = article_links.filter(
            Q(article__title__icontains=query)
            | Q(article__abstract__icontains=query)
            | Q(article__doi__icontains=query)
            | Q(article__pmid__icontains=query)
        )

    article_links = list(article_links)
    available_publication_types = _available_publication_types(article_links)
    user_state_map = {}
    if request.user.is_authenticated:
        user_state_map = {
            state.article_id: state
            for state in PubmedArticleUserState.objects.filter(
                user=request.user, article_id__in=[link.article_id for link in article_links]
            )
        }

    rows = []
    seen_article_ids = set()
    for link in article_links:
        if link.article_id in seen_article_ids:
            continue
        seen_article_ids.add(link.article_id)
        if publication_type and publication_type not in article_metadata_list(link.article, "publication_types"):
            continue
        if not _journal_article_matches_subject(link.article, subject):
            continue

        link.user_state = user_state_map.get(link.article_id)
        link.publication_types = article_metadata_list(link.article, "publication_types")
        link.mesh_terms = article_metadata_list(link.article, "mesh_terms")
        link.keywords = article_metadata_list(link.article, "keywords")
        rows.append(link)

    return {
        "rows": rows,
        "active_journals": active_journals,
        "selected_journal_ids": selected_journal_ids,
        "selected_month": selected_month,
        "month_options": _journal_month_options(),
        "query": query,
        "publication_type": publication_type,
        "subject": subject,
        "available_publication_types": available_publication_types,
        "can_recommend": can_recommend_pubmed_articles(request.user),
    }


def _journal_article_actions_context(request, article):
    user_state = None
    if request.user.is_authenticated:
        user_state = PubmedArticleUserState.objects.filter(user=request.user, article=article).first()
    return {
        "article": article,
        "user_state": user_state,
        "recommendation_count": article.user_states.filter(recommended_at__isnull=False).count(),
        "can_recommend": can_recommend_pubmed_articles(request.user),
        "next_url": request.POST.get("next") or request.GET.get("next") or request.get_full_path(),
    }


class JournalListView(TemplateView):
    template_name = "submissions/journal_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        browser_context = _journal_browser_rows(self.request)
        context.update(browser_context)
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
            return render(self.request, "submissions/fragments/journal_article_list.html", context, **response_kwargs)
        return super().render_to_response(context, **response_kwargs)


@login_required
@require_POST
def journal_article_toggle_star(request, article_id):
    article = get_object_or_404(PubmedArticle, pk=article_id)
    state, _ = PubmedArticleUserState.objects.get_or_create(user=request.user, article=article)
    state.starred_at = None if state.starred_at else timezone.now()
    state.save(update_fields=["starred_at", "modified"])

    if request.headers.get("HX-Request") == "true":
        return render(
            request,
            "submissions/fragments/journal_article_actions.html",
            _journal_article_actions_context(request, article),
        )
    return redirect(request.POST.get("next") or reverse("submissions:journal_list"))


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
