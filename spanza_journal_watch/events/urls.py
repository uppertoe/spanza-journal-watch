from django.urls import path

from . import views

app_name = "events"
urlpatterns = [
    path("pacs", views.LiveSessionListView.as_view(), name="session_list"),
    path("pacs/<slug:slug>", views.LiveSessionDetailView.as_view(), name="session_detail"),
]
