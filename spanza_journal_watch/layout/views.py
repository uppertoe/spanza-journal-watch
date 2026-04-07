import json

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET
from django.views.generic import DetailView, ListView

from spanza_journal_watch.analytics.models import PageView
from spanza_journal_watch.submissions.models import Review
from spanza_journal_watch.submissions.views import attach_review_display_fields
from spanza_journal_watch.utils.functions import get_domain_url
from spanza_journal_watch.utils.mixins import HtmxMixin, SidebarMixin

from .models import FeatureArticle, Homepage, PageHeader


class HomepageView(SidebarMixin, HtmxMixin, ListView):
    template_name = "layout/home.html"
    paginate_by = 5
    context_object_name = "reviews"

    # HTMX
    htmx_templates = [
        "layout/fragments/articles.html",
        "layout/fragments/home_pagination.html",
        "fragments/action_dock_oob.html",
    ]

    # Layout variables
    number_of_card_features = 2
    article_cols = 1
    feature_text_styles = ["text-primary", "text-secondary", "text-primary-emphasis", "text-success", "text_danger"]

    def get_queryset(self):
        self._homepage = Homepage.get_current_homepage()
        homepage = self._homepage
        subscriber_id = self.request.session.get("subscriber_id")
        PageView.record_view(homepage, subscriber_id, request=self.request)

        queryset = (
            Review.objects.filter(issues__homepage=homepage, active=True, is_featured=False)
            .select_related(
                "article",
                "article__journal",
                "author",
            )
            .prefetch_related("issues", "article__tags")
            .order_by("-created")
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        homepage = self._homepage
        domain = get_domain_url()

        context["card_features"] = homepage.get_card_features()[: self.number_of_card_features]
        attach_review_display_fields(context["card_features"])
        attach_review_display_fields(context["reviews"])
        context["article_cols"] = self.article_cols
        context["feature_text_styles"] = self.feature_text_styles
        context["page_title"] = "SPANZA Journal Watch"
        context["show_default_action_dock"] = False
        context["action_dock_aria_label"] = "Homepage quick navigation"
        context["page_meta_description"] = (
            "Review highlights from the paediatric anaesthesia literature"
            " curated by the SPANZA Journal Watch community."
        )
        context["canonical_url"] = self.request.build_absolute_uri(self.request.path)
        context["structured_data"] = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": "SPANZA Journal Watch",
                "url": f"{domain}/",
                "description": context["page_meta_description"],
                "potentialAction": {
                    "@type": "SearchAction",
                    "target": f"{domain}/search?q={{search_term_string}}",
                    "query-input": "required name=search_term_string",
                },
            }
        )

        # Override header
        override = {}
        header = PageHeader.get_active_for(PageHeader.PageType.HOME)
        context["page_header"] = header.collate_fields(**override) if header else override

        return context


class FeatureArticleDetailView(DetailView):
    model = FeatureArticle


@require_GET
@cache_control(max_age=60 * 60 * 24, immutable=True, public=True)  # one day
def favicon_file(request: HttpRequest) -> HttpResponse:
    """Serves favicons for various platforms"""
    name = request.path.lstrip("/")
    file = (settings.APPS_DIR / "static" / "images" / "favicon_package" / name).open("rb")
    return FileResponse(file)


@require_GET
def service_worker_view(request: HttpRequest) -> HttpResponse:
    """Serve the service worker from root scope with no-cache headers."""
    sw_path = settings.APPS_DIR / "static" / "js" / "sw.js"
    return FileResponse(
        sw_path.open("rb"),
        content_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@require_GET
@cache_control(max_age=86400, public=True)
def manifest_view(request: HttpRequest) -> JsonResponse:
    """PWA web app manifest with all required fields for installability."""
    manifest = {
        "id": "/",
        "name": "SPANZA Journal Watch",
        "short_name": "Journal Watch",
        "description": "Curated reviews of the paediatric anaesthesia literature by SPANZA members.",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "theme_color": "#152b3b",
        "background_color": "#152b3b",
        "icons": [
            {
                "src": static("images/favicon_package/android-chrome-192x192.png"),
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": static("images/favicon_package/android-chrome-512x512.png"),
                "sizes": "512x512",
                "type": "image/png",
            },
        ],
        "screenshots": [
            {
                "src": static("images/pwa/screenshot-wide.png"),
                "sizes": "1156x654",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Journal Watch desktop view",
            },
            {
                "src": static("images/pwa/screenshot-narrow.png"),
                "sizes": "760x1330",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Journal Watch mobile view",
            },
        ],
    }
    return JsonResponse(manifest, content_type="application/manifest+json")
