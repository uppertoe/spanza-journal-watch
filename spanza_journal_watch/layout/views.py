from django.views.generic import DetailView, ListView
from submissions.models import Review

from spanza_journal_watch.utils.mixins import HtmxMixin, SidebarMixin

from .models import FeatureArticle, Homepage


class HomepageView(SidebarMixin, HtmxMixin, ListView):
    template_name = "layout/home.html"
    paginate_by = 2
    context_object_name = "body_articles"

    # HTMX
    htmx_templates = ["layout/fragments/articles.html", "layout/fragments/article_pagination.html"]

    # Layout variables
    number_of_card_features = 2

    def get_queryset(self):
        homepage = Homepage.get_current_homepage()
        # queryset = homepage.issue.reviews.exclude(active=False).order_by("-created")
        queryset = (
            Review.objects.filter(issues__homepage=homepage, active=True)
            .prefetch_related("issues")
            .order_by("-created")
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        homepage = Homepage.get_current_homepage()
        context["main_feature"] = homepage.get_main_feature()
        context["card_features"] = homepage.get_card_features()[: self.number_of_card_features]
        return context


class FeatureArticleDetailView(DetailView):
    model = FeatureArticle
