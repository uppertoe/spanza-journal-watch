from django.test import RequestFactory, TestCase
from django.urls import reverse

from submissions.models import Issue, Review

from .models import FeatureArticle, Homepage
from .views import HomepageView


class FeatureArticleModelTest(TestCase):
    def test_str_representation(self):
        article = FeatureArticle(title="Test Article")
        self.assertEqual(str(article), "Test Article")


class HomepageViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.issue = Issue.objects.create(title="Test Issue")
        self.homepage = Homepage.objects.create(issue=self.issue)

    def test_get(self):
        url = reverse("homepage")
        request = self.factory.get(url)

        response = HomepageView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/home.html")

    def test_render_to_response(self):
        context = {
            "articles_html": "Articles HTML",
            "pagination_html": "Pagination HTML",
        }

        view = HomepageView()
        response = view.render_to_response(context)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "Articles HTMLPagination HTML")

    def test_get_main_feature_with_override(self):
        self.homepage.override_main = True
        feature_article = FeatureArticle.objects.create(title="Test Feature Article")
        self.homepage.main_feature = feature_article
        self.homepage.save()

        view = HomepageView()
        main_feature = view.get_main_feature()

        self.assertEqual(main_feature, feature_article)

    def test_get_main_feature_without_override(self):
        feature_article = FeatureArticle.objects.create(title="Test Feature Article")
        self.issue.main_feature = feature_article
        self.issue.save()

        view = HomepageView()
        main_feature = view.get_main_feature()

        self.assertEqual(main_feature, feature_article)

    def test_get_articles(self):
        review1 = Review.objects.create(title="Article 1", is_featured=True)
        review2 = Review.objects.create(title="Article 2", is_featured=False)
        review3 = Review.objects.create(title="Article 3", is_featured=False)
        self.issue.reviews.add(review1, review2, review3)

        view = HomepageView()
        articles = view.get_articles()

        self.assertEqual(articles["features"].count(), 1)
        self.assertEqual(articles["features"][0], review1)
        self.assertEqual(articles["body_articles"].count(), 3)
        self.assertListEqual(list(articles["body_articles"]), [review1, review2, review3])
