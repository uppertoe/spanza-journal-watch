from django.urls import path

from .views import FeatureArticleDetailView

app_name = "layout"
urlpatterns = [path("<slug:slug>/", FeatureArticleDetailView.as_view(), name="feature_article_detail")]
