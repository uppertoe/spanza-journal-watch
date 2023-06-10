from django.views.generic import ListView

from .models import Homepage


class HomepageView(ListView):
    template_name = "pages/home.html"
    paginate_by = 1
    homepage = Homepage.get_current_homepage()
    articles = homepage.get_articles()
    context_object_name = "body_articles"

    def get_queryset(self):
        queryset = self.articles.get("body_articles")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # context['issue'] = homepage.issue
        context["main_feature"] = self.homepage.get_main_feature()
        context["card_features"] = self.articles.get("features")
        return context
