from django.db.models import Count
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.generic import DetailView, ListView

from submissions.models import Issue, Tag

from .models import FeatureArticle, Homepage


class SidebarMixin:
    """
    Adds sidebar features to the context
    """

    # Layout variables
    number_of_sidebar_issues = 3
    number_of_tags = 8

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["issues"] = Issue.objects.exclude(active=False).order_by("-created")[: self.number_of_sidebar_issues]
        context["tags"] = (
            Tag.objects.exclude(active=False)
            .annotate(article_count=Count("articles"))
            .order_by("-article_count")[: self.number_of_tags]
        )
        return context


class HomepageView(SidebarMixin, ListView):
    template_name = "layout/home.html"
    paginate_by = 2
    context_object_name = "body_articles"

    # Layout variables
    number_of_card_features = 2

    def render_htmx_response(self):
        context = self.get_context_data()
        articles_html = render_to_string("layout/fragments/articles.html", context, request=self.request)
        pagination_html = render_to_string("layout/fragments/article_pagination.html", context, request=self.request)
        response = articles_html + pagination_html
        return HttpResponse(response)

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("HX-Request") == "true":
            return self.render_htmx_response()
        return super().render_to_response(context, **response_kwargs)

    def get_queryset(self):
        homepage = Homepage.get_current_homepage()
        queryset = homepage.issue.reviews.exclude(active=False).order_by("-created")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        homepage = Homepage.get_current_homepage()
        context["main_feature"] = homepage.get_main_feature()
        context["card_features"] = homepage.get_card_features()[: self.number_of_card_features]
        return context


class FeatureArticleDetailView(DetailView):
    model = FeatureArticle
