from django.urls import path

from . import views

app_name = "cpd"
urlpatterns = [
    path("record-read/", views.record_full_text_click, name="cpd_record_read"),
    path("toggle-tracking/", views.toggle_cpd_tracking, name="toggle_tracking"),
    path("", views.report_page, name="report_page"),
    path("generate/", views.generate_report, name="generate"),
    path("status/", views.report_status, name="report_status"),
    path("download/<int:report_id>/", views.download_report, name="download"),
    path("article-count/", views.article_count_preview, name="article_count"),
    path("read-ids/", views.read_article_ids, name="read_ids"),
]
