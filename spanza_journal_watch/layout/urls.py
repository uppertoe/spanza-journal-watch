from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "layout"
urlpatterns = [
    path("feature/<slug:slug>", views.FeatureArticleDetailView.as_view(), name="feature_article_detail"),
    path("sw.js", views.service_worker_view, name="service_worker"),
    path("manifest.json", views.manifest_view, name="manifest"),
    path("offline.html", TemplateView.as_view(template_name="offline.html"), name="offline"),
    path("android-chrome-192x192.png", views.favicon_file),
    path("android-chrome-512x512.png", views.favicon_file),
    path("apple-touch-icon.png", views.favicon_file),
    path("browserconfig.xml", views.favicon_file),
    path("favicon-16x16.png", views.favicon_file),
    path("favicon-32x32.png", views.favicon_file),
    path("favicon.ico", views.favicon_file),
    path("mstile-150x150.png", views.favicon_file),
    path("safari-pinned-tab.svg", views.favicon_file),
    path("site.webmanifest", views.favicon_file),
]
