from django.urls import path

from . import views

app_name = "backend"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("subscribers/upload", views.upload_subscriber_csv, name="upload_subscribers"),
    path("subscribers/upload/change-header/<str:save_token>", views.edit_csv_header, name="edit_csv_header"),
    path("subscribers/upload/process-csv/<str:save_token>", views.process_csv, name="process_csv"),
    path("newsletter/send/<str:send_token>", views.final_newsletter, name="final_newsletter"),
    path("newsletter/send/confirm/<str:send_token>", views.send_final_newsletter, name="send_final_newsletter"),
    path("newsletter/stats", views.newsletter_stats_list, name="newsletter_stats_list"),
    path("newsletter/stats/<int:pk>", views.newsletter_stats_detail, name="newsletter_stats_detail"),
]
