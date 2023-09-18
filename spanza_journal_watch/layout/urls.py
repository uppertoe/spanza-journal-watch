from django.urls import path

from . import views

app_name = "layout"
urlpatterns = [
    path("feature/<slug:slug>", views.FeatureArticleDetailView.as_view(), name="feature_article_detail"),
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
