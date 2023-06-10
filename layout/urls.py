from django.urls import path

from .views import FeatureArticleDetailView

urlpatterns = [path("<slug:slug>/", FeatureArticleDetailView.as_view(), name="feature_article_detail")]
