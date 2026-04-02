from django.urls import path

from spanza_journal_watch.users.views import (
    update_profile_name,
    user_detail_view,
    user_redirect_view,
    user_update_view,
)

app_name = "users"
urlpatterns = [
    path("~redirect/", view=user_redirect_view, name="redirect"),
    path("~update/", view=user_update_view, name="update"),
    path("~update-name/", view=update_profile_name, name="update_name"),
    path("<int:pk>/", view=user_detail_view, name="detail"),
]
