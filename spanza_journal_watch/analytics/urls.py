from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("pixel.png", views.track_email_open, name="track_email_open"),
    path("link", views.track_email_click, name="track_email_click"),
    path("link/<str:newsletter_token>", views.track_newsletter_link, name="track_newsletter_email_link"),
    path("page/<str:model>/<str:slug>", views.page_view, name="page_view"),
]
