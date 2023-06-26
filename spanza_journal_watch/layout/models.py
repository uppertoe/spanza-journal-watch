from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from spanza_journal_watch.submissions.models import Issue, Review
from spanza_journal_watch.utils.functions import HTMLShortener, unique_slugify
from spanza_journal_watch.utils.models import TimeStampedModel


class FeatureArticle(TimeStampedModel):
    # Constants
    TRUNCATED_BODY_LENGTH = 200

    # Fields
    title = models.CharField(max_length=255)
    body = models.TextField(null=True, blank=True)
    slug = models.SlugField(unique=True, blank=True)

    # Instance methods
    def get_truncated_body(self):
        # return shorten_text(self.body, self.TRUNCATED_BODY_LENGTH)
        return HTMLShortener(self.TRUNCATED_BODY_LENGTH).truncate_html(self.body)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, slugify(self.title))
        super().save(*args, **kwargs)

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

    # Relationships
    main_feature = models.ForeignKey(FeatureArticle, on_delete=models.CASCADE, blank=True, null=True)
