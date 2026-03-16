from urllib.parse import urlencode

from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import BaseBreadcrumbMixin, DetailBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.analytics.models import PageView
from spanza_journal_watch.layout.models import PageHeader
from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.functions import get_domain_url
from spanza_journal_watch.utils.mixins import HitMixin, HtmxMixin, SidebarMixin

from .models import Author, HealthService, Issue, Review, Tag


def build_share_urls(share_title, canonical_url):
    share_text = f"{share_title}\n\n{canonical_url}"
    return {
        "share_text": share_text,
        "bluesky_share_url": f"https://bsky.app/intent/compose?{urlencode({'text': share_text})}",
        "x_share_url": f"https://twitter.com/intent/tweet?{urlencode({'text': share_title, 'url': canonical_url})}",
        "facebook_share_url": f"https://www.facebook.com/sharer/sharer.php?{urlencode({'u': canonical_url})}",
        "email_share_url": f"mailto:?{urlencode({'subject': share_title, 'body': share_text})}",
    }


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
        domain = get_domain_url()
        canonical_url = f"{domain}{self.object.get_absolute_url()}"

        article_title = self.object.article.get_title().strip()
        share_title = f"SPANZA Journal Watch - {article_title}"
        share_description = self.object.get_truncated_body().strip()
        share_context = build_share_urls(share_title, canonical_url)

        context["canonical_url"] = canonical_url
        context["share_title"] = share_title
        context["share_description"] = share_description
        context["share_text"] = share_context["share_text"]
        context["share_image_url"] = f"{domain}{self.object.feature_image.url}" if self.object.feature_image else ""
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

        domain = get_domain_url()
        for review in context["articles"]:
            article_title = review.article.get_title().strip()
            review_share_title = f"SPANZA Journal Watch - {article_title}"
            review_canonical_url = f"{domain}{review.get_absolute_url()}"
            review_share_context = build_share_urls(review_share_title, review_canonical_url)

            review.share_title = review_share_title
            review.canonical_url = review_canonical_url
            review.share_text = review_share_context["share_text"]
            review.bluesky_share_url = review_share_context["bluesky_share_url"]
            review.x_share_url = review_share_context["x_share_url"]
            review.facebook_share_url = review_share_context["facebook_share_url"]
            review.email_share_url = review_share_context["email_share_url"]

        return context

    def get_queryset(self):
        return (
            Review.objects.filter(issues=self.object, active=True)
            .order_by("-created")
            .select_related("article__journal")
        )


class IssueListView(SidebarMixin, HtmxMixin, ListBreadcrumbMixin, ListView):
    model = Issue
    context_object_name = "issues"
    template_name = "submissions/issue_list.html"
    queryset = Issue.objects.exclude(active=False).order_by("-date")

    # HTMX
    htmx_templates = ["submissions/fragments/issues.html", "submissions/fragments/issue_list_pagination.html"]

    # Frontend options
    paginate_by = 5
    issue_cols = 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue_cols"] = self.issue_cols

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
        "articles", "articles__reviews", "articles__journal", "articles__reviews__author", "articles__reviews__issues"
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
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Override header
        header = PageHeader.get_active_for(PageHeader.PageType.TAG)
        override = {"title": str(self.object)}
        context["page_header"] = header.collate_fields(**override) if header else override

        context["article_cols"] = self.article_cols

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

        context.update(self.search(query, year=selected_year, tag_slugs=selected_tags))

        context["query"] = query
        context["selected_year"] = selected_year
        context["selected_tags"] = selected_tags

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

        return context

    def search(self, query, year="", tag_slugs=None):
        tag_slugs = tag_slugs or []

        if query:
            reviews = Review.search(query)
        else:
            reviews = (
                Review.objects.exclude(active=False)
                .select_related("article__journal", "author")
                .prefetch_related("article__tags")
                .order_by("-created")
            )

        if year and str(year).isdigit():
            reviews = reviews.filter(publish_date__year=int(year))

        if tag_slugs:
            reviews = reviews.filter(article__tags__slug__in=tag_slugs).distinct()

        result_count = reviews.count()

        # Keep search pages fast and readable
        results = {
            "result_reviews": reviews[:80],
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
    htmx_templates = ["layout/fragments/articles.html", "fragments/pagination.html"]

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

        # Supply only paginated objects to the template
        paginator = context["paginator"]
        page = context["page_obj"]
        context["reviews"] = paginator.get_page(page.number)

        return context

    def get_queryset(self):
        return (
            Review.objects.filter(author=self.object, active=True)
            .order_by("-created")
            .select_related("article__journal")
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
