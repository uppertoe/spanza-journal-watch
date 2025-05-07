from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views import defaults as default_views
from django.views.generic.base import TemplateView
from markdownx import urls as markdownx

from spanza_journal_watch.layout.models import AuthorSitemap, IssueSitemap, ReviewSitemap, TagSitemap
from spanza_journal_watch.layout.views import HomepageView

sitemaps = {
    "reviews": ReviewSitemap,
    "issues": IssueSitemap,
    "tags": TagSitemap,
    "authors": AuthorSitemap,
}

urlpatterns = [
    # Layout
    path("", HomepageView.as_view(), name="home"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # User management
    path("users/", include("spanza_journal_watch.users.urls", namespace="users")),
    path("accounts/", include("allauth.urls")),
    # Your stuff: custom urls includes go here
    path("", include("spanza_journal_watch.submissions.urls")),
    path("", include("spanza_journal_watch.layout.urls")),
    path("newsletter/", include("spanza_journal_watch.newsletter.urls")),
    path("analytics/", include("spanza_journal_watch.analytics.urls")),
    path("backend/", include("spanza_journal_watch.backend.urls")),
    # Third party urls
    path("tinymce/", include("tinymce.urls")),
    path("markdownx/", include(markdownx)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if not settings.DEBUG:
    urlpatterns += [
        path("anymail/", include("anymail.urls")),
    ]
else:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [path("__debug__/", include(debug_toolbar.urls))] + urlpatterns
