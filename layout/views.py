from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.generic import DetailView, ListView

from .models import FeatureArticle, Homepage


class HomepageView(ListView):
    template_name = "layout/home.html"
    paginate_by = 1
    context_object_name = "body_articles"
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
        articles = homepage.get_articles()
        queryset = articles.get("body_articles")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        homepage = Homepage.get_current_homepage()
        articles = homepage.get_articles()
        context["main_feature"] = homepage.get_main_feature()
        context["card_features"] = articles.get("features")[: self.number_of_card_features]
        return context


class FeatureArticleDetailView(DetailView):
    model = FeatureArticle
