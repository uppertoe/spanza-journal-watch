from django.contrib.sitemaps import Sitemap
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from spanza_journal_watch.submissions.models import Author, Issue, Review, Tag
from spanza_journal_watch.utils.celerytasks import celery_resize_image
from spanza_journal_watch.utils.functions import HTMLShortener, get_unique_slug
from spanza_journal_watch.utils.modelmethods import name_image
from spanza_journal_watch.utils.models import PageModel, TimeStampedModel


class FeatureArticle(TimeStampedModel):
    # Constants
    TRUNCATED_BODY_LENGTH = 200

    # Fields
    title = models.CharField(max_length=255)
    body = models.TextField(null=True, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    image = models.ImageField(
        upload_to=name_image,
        blank=True,
        null=True,
    )

    # Instance methods
    def get_truncated_body(self):
        return HTMLShortener(self.TRUNCATED_BODY_LENGTH).truncate_html(self.body)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.title))
        super().save(*args, **kwargs)

        # Delegate resizing to Celery
        celery_resize_image.delay(self.image.name)

    def get_absolute_url(self):
        return reverse("layout:feature_article_detail", kwargs={"slug": self.slug})

    # Special methods
    def __str__(self):
        return self.title


class Homepage(TimeStampedModel):
    CURRENT_HOMEPAGE = None

    # Fields
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)
    override_main = models.BooleanField(default=False)
    publication_ready = models.BooleanField(default=False)

    # Class methods
    @classmethod
    def publish_homepage(cls, homepage):
        # Called at startup in .apps
        if homepage.publication_ready:
            cls.CURRENT_HOMEPAGE = homepage
            print(f"Homepage set to {homepage}")
        else:
            print(f"{homepage} not set; publication_ready is false")

    @classmethod
    def get_current_homepage(cls):
        if cls.CURRENT_HOMEPAGE is None:
            latest_homepage = cls.objects.filter(publication_ready=True).order_by("-created").first()
            if latest_homepage:
                cls.publish_homepage(latest_homepage)
        return cls.CURRENT_HOMEPAGE

    # Instance methods
    def get_main_feature(self):
        if self.override_main:
            return self.main_feature
        return self.issue.get_main_feature()

    def get_card_features(self):
        card_features = Review.objects.filter(issues__homepage=self, is_featured=True, active=True).order_by("created")
        return card_features

    # Special methods
    def __str__(self):
        return f"{self.issue} homepage - {self.created}"


class PageHeader(PageModel):
    class PageType(models.TextChoices):
        HOME = "home", "Home"
        SEARCH = "search", "Search"
        ISSUE_LIST = "issue_list", "Issue list"
        ISSUE_DETAIL = "issue_detail", "Issue detail"
        REVIEW_DETAIL = "review_detail", "Review detail"
        TAG = "tag", "Tag pages"

    page_type = models.CharField(max_length=32, choices=PageType.choices, db_index=True)

    @classmethod
    def get_active_for(cls, page_type):
        return cls.objects.filter(page_type=page_type, active=True).order_by("-modified").first()

    def __str__(self):
        return f"{self.get_page_type_display()}: {self.feature_article}"


# Sitemaps
# -----------


class ReviewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.9

    def items(self):
        return Review.objects.filter(active=True).order_by("-created")

    def lastmod(self, obj):
        return obj.modified


class IssueSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return Issue.objects.filter(active=True).order_by("-created")

    def lastmod(self, obj):
        return obj.modified


class TagSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return Tag.objects.all().order_by("text")


class AuthorSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.4

    def items(self):
        return Author.objects.filter(anonymous=False).order_by("name")
