from django.contrib.postgres.search import SearchQuery
from django.db.models import Q
from django.http import Http404
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import DetailBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.utils.mixins import HtmxMixin, PageviewMixin, SidebarMixin

from .models import Article, Issue, Journal, Review, Tag


class ReviewDetailView(PageviewMixin, SidebarMixin, DetailBreadcrumbMixin, DetailView):
    model = Review
    context_object_name = "review"
    template_name = "submissions/review_detail.html"

    # Breadcrumb
    breadcrumb_use_pk = False


class IssueDetailView(PageviewMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, DetailBreadcrumbMixin, ListView):
    template_name = "submissions/issue_detail.html"
    model = Issue

    # Breadcrumb
    breadcrumb_use_pk = False

    # HTMX
    htmx_templates = ["submissions/fragments/article_card.html", "submissions/fragments/article_pagination.html"]

    # Frontend options
    paginate_by = 5
    article_cols = 1

    def get(self, request, *args, **kwargs):
        self.object = self.get_object(queryset=Issue.objects.exclude(active=False))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue"] = self.object
        context["article_cols"] = self.article_cols

        # Supply only paginated objects to the template
        paginator = context["paginator"]
        page = context["page_obj"]
        context["articles"] = paginator.get_page(page.number)
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
    queryset = Issue.objects.exclude(active=False).order_by("-created")

    # HTMX
    htmx_templates = ["submissions/fragments/issues.html", "submissions/fragments/article_pagination.html"]

    # Frontend options
    paginate_by = 5
    issue_cols = 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue_cols"] = self.issue_cols
        return context


class TagListView(ListBreadcrumbMixin, ListView):
    model = Tag
    context_object_name = "tag_list"
    template_name = "tags/tag_list.html"
    queryset = Tag.objects.exclude(active=False).order_by("text")

    # Breadcrumb
    breadcrumb_use_pk = False


class TagDetailView(SidebarMixin, DetailBreadcrumbMixin, DetailView):
    model = Tag
    context_object_name = "tag"
    template_name = "submissions/tag_detail.html"

    # Breadcrumb
    breadcrumb_use_pk = False


class LatestIssueView(RedirectView):
    permanent = False
    query_string = False

    def get_redirect_url(self, *args, **kwargs):
        issue = Issue.objects.exclude(active=False).first()
        if not issue:
            raise Http404
        return issue.get_absolute_url()


class SearchView(SidebarMixin, HtmxMixin, TemplateView):
    template_name = "submissions/search.html"

    # HTMX
    htmx_templates = ["submissions/fragments/search_results.html"]

    # Search settings
    rank_threshold = 0.1
    no_result_message = "No results found"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q")
        if query:
            context.update(self.search(query))
            context["tags"] = Tag.objects.filter(Q(text__icontains=query))
        return context

    def search(self, query):
        search_query = SearchQuery(query)
        rank = self.rank_threshold

        articles = Article.search(search_query, rank=rank)
        reviews = Review.search(search_query, rank=rank)
        journals = Journal.search(search_query, rank=rank)

        results = {
            "result_articles": articles,
            "result_reviews": reviews,
            "result_journals": journals,
        }

        # Add a message if all querysets are empty
        if not any(queryset for queryset in [articles, reviews, journals] if queryset.exists()):
            results["no_result_message"] = self.no_result_message

        return results
