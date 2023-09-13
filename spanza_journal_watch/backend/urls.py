from django.urls import path

from . import views

app_name = "backend"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("subscribers/upload", views.upload_subscriber_csv, name="upload_subscribers"),
]
