from django.http import Http404
from django.views.generic import DetailView, ListView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin

from spanza_journal_watch.utils.mixins import HtmxMixin, PageviewMixin, SidebarMixin

from .models import Issue, Review, Tag


class ReviewDetailView(PageviewMixin, SidebarMixin, DetailView):
    model = Review
    context_object_name = "review"
    template_name = "submissions/review_detail.html"


class ReviewListView(ListView):
    model = Review
    context_object_name = "review_list"
    template_name = "reviews/review_list.html"
    queryset = Review.objects.exclude(active=False).order_by("-created")


class IssueDetailView(PageviewMixin, SidebarMixin, HtmxMixin, SingleObjectMixin, ListView):
    template_name = "submissions/issue_detail.html"
    context_object_name = "context_object"

    # HTMX
    htmx_templates = ["submissions/fragments/article_card.html", "submissions/fragments/article_pagination.html"]

    # Frontend options
    paginate_by = 3
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
        return self.object.reviews.exclude(active=False).order_by("-created")


class IssueListView(SidebarMixin, HtmxMixin, ListView):
    model = Issue
    context_object_name = "issues"
    template_name = "submissions/issue_list.html"
    queryset = Issue.objects.exclude(active=False).order_by("-created")

    # HTMX
    htmx_templates = ["submissions/fragments/issues.html", "submissions/fragments/article_pagination.html"]

    # Frontend options
    paginate_by = 3
    issue_cols = 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue_cols"] = self.issue_cols
        return context


class TagListView(ListView):
    model = Tag
    context_object_name = "tag_list"
    template_name = "tags/tag_list.html"
    queryset = Tag.objects.exclude(active=False).order_by("text")


class TagDetailView(SidebarMixin, DetailView):
    model = Tag
    context_object_name = "tag"
    template_name = "submissions/tag_detail.html"


class LatestIssueView(RedirectView):
    permanent = False
    query_string = True

    def get_redirect_url(self, *args, **kwargs):
        issue = Issue.objects.exclude(active=False).first()
        if not issue:
            raise Http404
        return issue.get_absolute_url()
