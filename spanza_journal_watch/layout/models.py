from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from spanza_journal_watch.submissions.models import Issue, Review
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
        return reverse("feature_article_detail", kwargs={"slug": self.slug})

    # Special methods
    def __str__(self):
        return self.title


class Homepage(TimeStampedModel):
    CURRENT_HOMEPAGE = None

    # Fields
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE)
    override_main = models.BooleanField(default=False)
    publication_ready = models.BooleanField(default=False)
    # Relationships
    homepage_page = models.ForeignKey("HomepagePage", on_delete=models.CASCADE, blank=True, null=True)

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


# Individual related_names to avoid clashes
class SearchPage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="search_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="search_page_dark"
    )


class IssuePage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="issue_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="issue_page_dark"
    )


class IssueDetailPage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="issue_detail_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="issue_detail_page_dark"
    )


class ReviewPage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="review_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="review_page_dark"
    )


class TagPage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="tag_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="tag_page_dark"
    )


class HomepagePage(PageModel):
    light_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="homepage_page_light"
    )
    dark_gradient = models.ForeignKey(
        "Gradient", on_delete=models.CASCADE, null=True, blank=True, related_name="homepage_page_dark"
    )

    def save(self, *args, **kwargs):
        # Refresh the homepage
        try:
            latest_homepage = Homepage.objects.filter(publication_ready=True).latest("created")
            Homepage.publish_homepage(latest_homepage)
        except:  # noqa
            print("Skipped Homepage import")

        return super().save(*args, **kwargs)


class Gradient(models.Model):
    name = models.CharField(max_length=255)
    css = models.TextField()

    def __str__(self):
        return self.name
