from django.urls import path

from . import views

app_name = "newsletter"
urlpatterns = [
    path("unsubscribe/<str:unsubscribe_token>", views.unsubscribe, name="unsubscribe"),
    path("unsubscribe/<str:unsubscribe_token>/confirm", views.confirm_unsubscribe, name="confirm-unsubscribe"),
    path("success", views.success, name="success"),
    path("subscribe", views.subscribe, name="subscribe"),
    path("drawer-unsubscribe", views.drawer_unsubscribe, name="drawer_unsubscribe"),
    path("toggle", views.toggle_subscription, name="toggle_subscription"),
]
