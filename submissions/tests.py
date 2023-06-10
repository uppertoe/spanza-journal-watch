from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from layout.models import FeatureArticle

from .models import Article, Comment, Hit, Issue, Journal, Review, Tag

User = get_user_model()


class TagModelTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(text="Test Tag")

    def test_str_representation(self):
        self.assertEqual(str(self.tag), "Test Tag")

    def test_save_method_generates_slug(self):
        self.assertEqual(self.tag.slug, "test-tag")

    def test_get_absolute_url(self):
        url = reverse("tag-detail", kwargs={"slug": self.tag.slug})
        self.assertEqual(self.tag.get_absolute_url(), url)

    def test_all_tags_list(self):
        # Add articles with tags for testing
        article1 = Article.objects.create(name="Article 1", journal=Journal.objects.create(name="Journal 1"))
        article1.tags.add(self.tag)
        article2 = Article.objects.create(name="Article 2", journal=Journal.objects.create(name="Journal 2"))
        article2.tags.add(self.tag)

        # Exclude inactive tags and order by article count
        expected_tags = [str(self.tag)]
        self.assertEqual(Tag.all_tags_list(), expected_tags)

    def test_delete_if_orphaned_deletes_unused_tag(self):
        # Create a tag without any articles
        tag = Tag.objects.create(text="Unused Tag")
        tag.delete_if_orphaned()
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())

        # Create a tag with an article
        article = Article.objects.create(name="Article", journal=Journal.objects.create(name="Journal"))
        article.tags.add(tag)
        tag.delete_if_orphaned()
        self.assertTrue(Tag.objects.filter(pk=tag.pk).exists())


class JournalModelTest(TestCase):
    def setUp(self):
        self.journal = Journal.objects.create(name="Test Journal")

    def test_str_representation(self):
        self.assertEqual(str(self.journal), "Test Journal")

    def test_save_method_generates_slug(self):
        self.assertEqual(self.journal.slug, "test-journal")


class ArticleModelTest(TestCase):
    def setUp(self):
        self.journal = Journal.objects.create(name="Test Journal")
        self.article = Article.objects.create(name="Test Article", journal=self.journal)

    def test_str_representation(self):
        self.assertEqual(str(self.article), "Test Article")

    def test_save_method_creates_tags(self):
        self.article.tags_string = "Tag1 #Tag2"
        self.article.save()
        self.assertEqual(self.article.tags.count(), 2)

    def test_save_method_updates_existing_tags(self):
        # Create a tag
        tag = Tag.objects.create(text="Tag1")
        self.article.tags.add(tag)

        # Update the tags_string
        self.article.tags_string = "Tag2 #Tag3"
        self.article.save()

        # Check that the tag count remains the same and new tags are created
        self.assertEqual(self.article.tags.count(), 3)

    def test_shortened_name(self):
        self.article.name = "Very long article name" * 10
        shortened_name = "Very long article name" * 5 + "..."
        self.assertEqual(self.article.shortened_name(), shortened_name)

    def test_tags_list(self):
        self.article.tags_string = "Tag1 #Tag2"
        tags_list = ["tag1", "tag2"]
        self.assertEqual(self.article.tags_list(), tags_list)


class ReviewModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpassword")
        self.journal = Journal.objects.create(name="Test Journal")
        self.article = Article.objects.create(name="Test Article", journal=self.journal)
        self.review = Review.objects.create(article=self.article, author=self.user, body="Test Body")

    def test_str_representation(self):
        self.assertEqual(str(self.review), "Review: Test Article")

    def test_save_method_generates_slug(self):
        self.assertEqual(self.review.slug, "test-article")

    def test_get_absolute_url(self):
        url = reverse("review-detail", kwargs={"slug": self.review.slug})
        self.assertEqual(self.review.get_absolute_url(), url)


class IssueModelTest(TestCase):
    def setUp(self):
        self.issue = Issue.objects.create(name="Test Issue", date="2023-01-01")

    def test_str_representation(self):
        self.assertEqual(str(self.issue), "Test Issue")

    def test_save_method_generates_slug(self):
        self.assertEqual(self.issue.slug, "test-issue")

    def test_get_card_features(self):
        # Create featured reviews for the issue
        review1 = Review.objects.create(article=Article.objects.create(name="Article 1"), is_featured=True)
        review2 = Review.objects.create(article=Article.objects.create(name="Article 2"), is_featured=True)
        self.issue.reviews.add(review1, review2)

        # Check that only featured reviews are returned
        features = self.issue.get_card_features()
        self.assertEqual(len(features), 2)
        self.assertIn(review1, features)
        self.assertIn(review2, features)

    def test_get_main_feature(self):
        # Create a main feature article for the issue
        feature_article = FeatureArticle.objects.create(name="Main Feature Article")
        self.issue.main_feature = feature_article

        # Check that the correct main feature article is returned
        self.assertEqual(self.issue.get_main_feature(), feature_article)


class CommentModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpassword")
        self.journal = Journal.objects.create(name="Test Journal")
        self.article = Article.objects.create(name="Test Article", journal=self.journal)
        self.comment = Comment.objects.create(body="Test Body", article=self.article, author=self.user)

    def test_str_representation(self):
        self.assertEqual(str(self.comment), "Comment")

    def test_comment_author(self):
        self.assertEqual(self.comment.author, self.user)

    def test_comment_article(self):
        self.assertEqual(self.comment.article, self.article)


class HitModelTest(TestCase):
    def setUp(self):
        self.journal = Journal.objects.create(name="Test Journal")
        self.article = Article.objects.create(name="Test Article", journal=self.journal)
        self.hit = Hit.objects.create(content_object=self.article)

    def test_update_page_count(self):
        initial_count = self.hit.count
        Hit.update_page_count(self.article)
        self.assertEqual(self.hit.count, initial_count + 1)

    def test_get_count(self):
        self.assertEqual(Hit.get_count(self.article), self.hit.count)
