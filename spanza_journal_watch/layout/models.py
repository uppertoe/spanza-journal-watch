import logging

from django.contrib.sitemaps import Sitemap
from django.core.cache import cache
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from spanza_journal_watch.submissions.models import Author, CuratedCollection, Issue, Review, Tag
from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.celerytasks import celery_resize_image
from spanza_journal_watch.utils.functions import HTMLShortener, get_unique_slug
from spanza_journal_watch.utils.modelmethods import name_image
from spanza_journal_watch.utils.models import PageModel, TimeStampedModel

logger = logging.getLogger(__name__)


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

    @property
    def image_width_safe(self):
        if not self.image:
            return None

        try:
            return self.image.width
        except (FileNotFoundError, OSError, ValueError):
            return None

    @property
    def image_height_safe(self):
        if not self.image:
            return None

        try:
            return self.image.height
        except (FileNotFoundError, OSError, ValueError):
            return None

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.title))
        super().save(*args, **kwargs)

        # Delegate resizing to Celery
        if self.image:
            celery_resize_image.delay(
                "layout.FeatureArticle",
                self.pk,
                "image",
                target_format="webp",
                variant_widths=(240, 480),
            )

    def get_absolute_url(self):
        return reverse("layout:feature_article_detail", kwargs={"slug": self.slug})

    # Special methods
    def __str__(self):
        return self.title


HOMEPAGE_CACHE_KEY = "layout:current_homepage_pk"


class Homepage(TimeStampedModel):
    # Fields
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)
    publication_ready = models.BooleanField(default=False)

    # Class methods
    @classmethod
    def publish_homepage(cls, homepage):
        if homepage.publication_ready:
            cache.set(HOMEPAGE_CACHE_KEY, homepage.pk, timeout=None)
            logger.info("Homepage set to %s", homepage)
        else:
            logger.warning("%s not set; publication_ready is false", homepage)

    @classmethod
    def get_current_homepage(cls):
        pk = cache.get(HOMEPAGE_CACHE_KEY)
        if pk is not None:
            try:
                return cls.objects.select_related("issue").get(pk=pk)
            except cls.DoesNotExist:
                cache.delete(HOMEPAGE_CACHE_KEY)
        # Cache miss: load latest publication-ready homepage and cache it.
        homepage = cls.objects.filter(publication_ready=True).order_by("-created").first()
        if homepage:
            cache.set(HOMEPAGE_CACHE_KEY, homepage.pk, timeout=None)
        return homepage

    def get_card_features(self):
        card_features = (
            Review.objects.filter(issues__homepage=self, is_featured=True, active=True)
            .select_related("article", "author")
            .prefetch_related("article__tags")
            .order_by("created")
        )
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
        cache_version = get_content_cache_version()
        cache_key = f"layout:page_header:v{cache_version}:type:{page_type}"
        return cache.get_or_set(
            cache_key,
            lambda: (
                cls.objects.select_related("feature_article")
                .filter(page_type=page_type, active=True)
                .order_by("-modified")
                .first()
            ),
            timeout=60 * 30,
        )

    def __str__(self):
        return f"{self.get_page_type_display()}: {self.feature_article}"


# Sitemaps
# -----------


class ReviewSitemap(Sitemap):
    changefreq = "yearly"
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
    """Curated topics with at least one live review: the same set /explore lists.

    Uncurated or empty topics used to be submitted too, which handed search
    engines pages the site itself does not link to and that have nothing on
    them.
    """

    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return (
            Tag.objects.filter(active=True, curated=True)
            .annotate(
                review_count=models.Count(
                    "articles__reviews", filter=models.Q(articles__reviews__active=True), distinct=True
                ),
                latest_review=models.Max(
                    "articles__reviews__modified", filter=models.Q(articles__reviews__active=True)
                ),
            )
            .filter(review_count__gt=0)
            .order_by("text")
        )

    def lastmod(self, obj):
        return obj.latest_review


class AuthorSitemap(Sitemap):
    """Named contributors with at least one live review, matching the /about listing."""

    changefreq = "monthly"
    priority = 0.4

    def items(self):
        return (
            Author.objects.filter(anonymous=False)
            .annotate(review_count=models.Count("reviews", filter=models.Q(reviews__active=True), distinct=True))
            .filter(review_count__gt=0)
            .order_by("name")
        )

    def lastmod(self, obj):
        return obj.modified


class CollectionSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return CuratedCollection.objects.filter(active=True).order_by("title")


class StaticPagesSitemap(Sitemap):
    """The index pages, which were previously reachable only by following links."""

    changefreq = "weekly"

    _pages = (
        ("home", 1.0),
        ("submissions:issue_list", 0.8),
        ("submissions:tag_list", 0.8),
        ("submissions:journal_list", 0.5),
        ("submissions:about", 0.5),
        ("events:session_list", 0.5),
    )

    def items(self):
        return [name for name, _priority in self._pages]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return dict(self._pages)[item]
