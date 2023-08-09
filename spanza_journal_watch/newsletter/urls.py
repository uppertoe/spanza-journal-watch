from django.urls import path

from . import views

app_name = "newsletter"
urlpatterns = [
    path("unsubscribe/<str:unsubscribe_token>", views.unsubscribe, name="unsubscribe"),
    path("success", views.success, name="success"),
    path("subscribe", views.subscribe, name="subscribe"),
]
