from http import HTTPStatus

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from submissions.models import Article, Issue, Review

from .models import FeatureArticle, Homepage


class FeatureArticleModelTest(TestCase):
    def test_save_generates_unique_slug(self):
        # Create a feature article
        article1 = FeatureArticle.objects.create(title="Test Article")
        article1.save()

        # Create another feature article with the same title
        article2 = FeatureArticle.objects.create(title="Test Article")
        article2.save()

        self.assertNotEqual(article1.slug, article2.slug)

    def test_get_absolute_url(self):
        article = FeatureArticle.objects.create(title="Test Article")
        url = reverse("feature_article_detail", kwargs={"slug": article.slug})

        self.assertEqual(article.get_absolute_url(), url)


class HomepageModelTest(TestCase):
    def setUp(self):
        self.issue = Issue.objects.create(name="Test Issue")
        self.homepage = Homepage.objects.create(issue=self.issue)

    def test_publish_homepage_sets_current_homepage(self):
        self.homepage.publication_ready = True
        self.homepage.save()

        Homepage.publish_homepage(self.homepage)

        self.assertEqual(Homepage.get_current_homepage(), self.homepage)

    def test_publish_homepage_does_not_set_current_homepage_if_not_publication_ready(self):
        Homepage.CURRENT_HOMEPAGE = None
        self.homepage.publication_ready = False
        self.homepage.save()

        Homepage.publish_homepage(self.homepage)

        self.assertIsNone(Homepage.get_current_homepage())

    def test_get_main_feature_with_override(self):
        self.homepage.override_main = True
        feature_article = FeatureArticle.objects.create(title="Test Feature Article")
        self.homepage.main_feature = feature_article
        self.homepage.save()

        main_feature = self.homepage.get_main_feature()

        self.assertEqual(main_feature, feature_article)

    def test_get_main_feature_without_override(self):
        feature_article = FeatureArticle.objects.create(title="Test Feature Article")
        self.issue.main_feature = feature_article
        self.issue.save()

        main_feature = self.homepage.get_main_feature()

        self.assertEqual(main_feature, feature_article)

    def test_get_articles(self):
        article1 = Article.objects.create(name="Article 1")
        article2 = Article.objects.create(name="Article 2")
        article3 = Article.objects.create(name="Article 3")
        review1 = Review.objects.create(article=article1, is_featured=True, active=True)
        review2 = Review.objects.create(article=article2, is_featured=False, active=True)
        review3 = Review.objects.create(article=article2, is_featured=False, active=True)
        review4 = Review.objects.create(article=article3, is_featured=True, active=False)
        self.issue.reviews.add(review1, review2, review3, review4)

        articles = self.homepage.get_articles()

        self.assertEqual(articles["features"].count(), 1)
        self.assertEqual(articles["features"][0], review1)
        self.assertEqual(articles["body_articles"].count(), 3)
        self.assertListEqual(list(articles["body_articles"]), [review1, review2, review3])


class FaviconFileTests(SimpleTestCase):
    def test_get(self):
        names = [
            "android-chrome-192x192.png",
            "android-chrome-512x512.png",
            "apple-touch-icon.png",
            "browserconfig.xml",
            "favicon-16x16.png",
            "favicon-32x32.png",
            "favicon.ico",
            "mstile-150x150.png",
            "safari-pinned-tab.svg",
            "site.webmanifest",
        ]

        for name in names:
            with self.subTest(name):
                response = self.client.get(f"/{name}")

                self.assertEqual(response.status_code, HTTPStatus.OK)
                self.assertEqual(
                    response["Cache-Control"],
                    "max-age=86400, immutable, public",
                )
                self.assertGreater(len(response.getvalue()), 0)
