from django.urls import path

from . import views

urlpatterns = [
    path("unsubscribe/<str:unsubscribe_token>", views.unsubscribe, name="unsubscribe"),
]
