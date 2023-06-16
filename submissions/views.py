from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.views.generic import DetailView, ListView
from django.views.generic.base import RedirectView
from django.views.generic.detail import SingleObjectMixin

from layout.views import SidebarMixin

from .models import Hit, Issue, Review, Tag


class PageviewMixin:
    """
    Takes the obj and stores it in the session
    in the form of {obj.model_name: obj.id}
    If an obj.id is not present, call Hit.update_page_count
    """

    def get_object(self, **kwargs):
        obj = super().get_object(**kwargs)
        model_class = str(obj.__class__.__name__).lower()
        model_str = f"model_{model_class}_viewed"
        viewed_objects = self.request.session.get(model_str, [])
        if obj.id not in viewed_objects:
            Hit.update_page_count(obj)
            viewed_objects.append(obj.id)
        self.request.session[model_str] = viewed_objects
        print(f"{model_str}: {viewed_objects}")
        return obj


class HtmxMixin:
    article_template = ""
    pagination_template = ""

    def render_htmx_response(self):
        context = self.get_context_data()
        articles_html = render_to_string(self.article_template, context, request=self.request)
        pagination_html = render_to_string(self.pagination_template, context, request=self.request)
        response = articles_html + pagination_html
        return HttpResponse(response)

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("HX-Request") == "true":
            return self.render_htmx_response()
        return super().render_to_response(context, **response_kwargs)


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
    article_template = "submissions/fragments/article_card.html"
    pagination_template = "submissions/fragments/article_pagination.html"

    # Frontend options
    paginate_by = 4
    article_cols = 1

    def get(self, request, *args, **kwargs):
        self.object = self.get_object(queryset=Issue.objects.exclude(active=False))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issue"] = self.object
        context["article_cols"] = self.article_cols
        return context

    def get_queryset(self):
        return self.object.reviews.exclude(active=False).order_by("-created")


class IssueListView(SidebarMixin, HtmxMixin, ListView):
    model = Issue
    context_object_name = "issues"
    template_name = "submissions/issue_list.html"
    queryset = Issue.objects.exclude(active=False).order_by("-created")

    # HTMX
    article_template = "submissions/fragments/issues.html"
    pagination_template = "submissions/fragments/article_pagination.html"

    # Frontend options
    paginate_by = 2
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
        print(f"here's an {issue}")
        if not issue:
            raise Http404
        return issue.get_absolute_url()
