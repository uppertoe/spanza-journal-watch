from django.views.generic import DetailView, ListView

from spanza_journal_watch.analytics.models import PageView
from spanza_journal_watch.submissions.models import Review
from spanza_journal_watch.utils.mixins import HtmxMixin, SidebarMixin

from .models import FeatureArticle, Homepage


class HomepageView(SidebarMixin, HtmxMixin, ListView):
    template_name = "layout/home.html"
    paginate_by = 5
    context_object_name = "reviews"

    # HTMX
    htmx_templates = ["layout/fragments/articles.html", "fragments/pagination.html"]

    # Layout variables
    number_of_card_features = 2
    article_cols = 1
    feature_text_styles = ["text-primary", "text-secondary", "text-primary-emphasis", "text-success", "text_danger"]

    def get_queryset(self):
        homepage = Homepage.get_current_homepage()
        subscriber_id = self.request.session.get("subscriber_id")
        PageView.record_view(homepage, subscriber_id)

        queryset = (
            Review.objects.filter(issues__homepage=homepage, active=True, is_featured=False)
            .select_related(
                "article",
                "article__journal",
                "author",
            )
            .prefetch_related("issues")
            .order_by("-created")
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        homepage = Homepage.get_current_homepage()

        context["card_features"] = homepage.get_card_features()[: self.number_of_card_features]
        context["article_cols"] = self.article_cols
        context["feature_text_styles"] = self.feature_text_styles

        # Override header
        override = {}
        header = homepage.homepage_page
        context["page_header"] = header.collate_fields(**override) if header else override

        return context


class FeatureArticleDetailView(DetailView):
    model = FeatureArticle
