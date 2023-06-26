from django.db.models import Q
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin
from view_breadcrumbs import BaseBreadcrumbMixin, DetailBreadcrumbMixin, ListBreadcrumbMixin

from spanza_journal_watch.utils.mixins import HitMixin, HtmxMixin, SidebarMixin

from .models import Issue, Review, Tag


class ReviewDetailView(HitMixin, SidebarMixin, BaseBreadcrumbMixin, DetailView):
    model = Review
    context_object_name = "review"
    template_name = "submissions/review_detail.html"

    # Breadcrumb
    @cached_property
    def crumbs(self):
        issue = Issue.objects.filter(reviews=self.object).latest("created")

        return [("Issues", reverse("submissions:issue_list")), (issue, issue.get_absolute_url()), (self.object, "")]


class IssueDetailView(HitMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, DetailBreadcrumbMixin, ListView):
    template_name = "submissions/issue_detail.html"
    model = Issue

    # Breadcrumb
    breadcrumb_use_pk = False

    # HTMX
    htmx_templates = ["submissions/fragments/article_card.html", "submissions/fragments/article_pagination.html"]

    # Frontend options
    paginate_by = 6
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
        query = self.request.GET.get("q")
        if query:
            context.update(self.search(query))
            context["tags"] = Tag.objects.filter(Q(text__icontains=query))
        return context

    def search(self, query):
        reviews = Review.search(query)
        results = {"result_reviews": reviews}

        if not reviews.exists():
            # Add a message if no results
            results["no_result_message"] = self.no_result_message

        return results


def ajax_get_tags(request):
    tags_queryset = Tag.get_all_tags()
    tags_list = [str(tag) for tag in tags_queryset]
    data = {"tags": tags_list}
    return JsonResponse(data)
