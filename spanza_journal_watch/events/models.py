import logging

from django.contrib.sitemaps import Sitemap
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from markdownx.models import MarkdownxField
from markdownx.utils import markdownify

from spanza_journal_watch.submissions.models import sanitize_markdown_html
from spanza_journal_watch.utils.functions import get_unique_slug, shorten_text
from spanza_journal_watch.utils.models import TimeStampedModel

logger = logging.getLogger(__name__)


class LiveSession(TimeStampedModel):
    TRUNCATED_BODY_LENGTH = 200

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(blank=True, null=True)
    speaker = models.CharField(max_length=255, blank=True)
    chair = models.CharField(max_length=255, blank=True)
    description = MarkdownxField(blank=True)
    pre_reading_note = models.CharField(
        max_length=255,
        blank=True,
        help_text="Short line shown above the pre-reading list, e.g. 'Pre-reading is not essential.'",
    )
    event_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Link to the SPANZA event page with full event information.",
    )
    registration_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Link to the online registration form.",
    )
    registration_note = models.TextField(
        blank=True,
        help_text="Short registration summary shown with the register button, e.g. fees and inclusions.",
    )
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("display_order", "start_datetime")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.title))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("events:session_detail", kwargs={"slug": self.slug})

    def get_markdown_description(self):
        return sanitize_markdown_html(markdownify(self.description)) if self.description else ""

    def get_plain_description(self):
        return strip_tags(self.get_markdown_description()).strip()

    def get_truncated_description(self):
        return shorten_text(self.get_plain_description(), self.TRUNCATED_BODY_LENGTH)

    @property
    def is_past(self):
        reference = self.end_datetime or self.start_datetime
        return reference < timezone.now()


class LiveSessionReading(TimeStampedModel):
    """One pre-reading paper for a session, with an optional discussion prompt.

    The paper itself resolves in order: a Journal Watch review, then an article
    from the database, then the manual citation fields (for papers held in
    neither).
    """

    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name="readings")
    review = models.ForeignKey(
        "submissions.Review",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_readings",
        help_text="Journal Watch review of the paper, if one exists.",
    )
    article = models.ForeignKey(
        "backend.PubmedArticle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_readings",
        help_text="Paper in the article database, if it has no review.",
    )
    topic = models.CharField(
        max_length=120, blank=True, help_text="Short discussion-topic label shown above the paper."
    )
    prompt = models.TextField(blank=True, help_text="Discussion questions posed to readers ahead of the session.")
    title = models.CharField(max_length=500, blank=True, help_text="Paper title, for papers not linked above.")
    source = models.CharField(max_length=255, blank=True, help_text="Journal and year, for papers not linked above.")
    url = models.URLField(max_length=500, blank=True, help_text="Full-text link, for papers not linked above.")
    pubmed_url = models.URLField(max_length=500, blank=True, help_text="PubMed link, for papers not linked above.")
    review_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Link to a Journal Watch review, if not linked above.",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("display_order", "id")

    def __str__(self):
        return self.get_title() or f"Reading {self.pk}"

    @property
    def linked_article(self):
        if self.review:
            return self.review.article
        return self.article

    def get_title(self):
        linked = self.linked_article
        return linked.get_title() if linked else self.title

    def get_source(self):
        linked = self.linked_article
        if linked:
            journal = str(linked.journal) if linked.journal else linked.source_journal_name
            return f"{journal}, {linked.year}" if linked.year else journal
        return self.source

    def get_review_url(self):
        if self.review:
            return self.review.get_absolute_url()
        return self.review_url

    def get_fulltext_url(self):
        linked = self.linked_article
        if linked and linked.article_url:
            return linked.article_url
        return self.url

    def get_pubmed_url(self):
        linked = self.linked_article
        if linked and linked.pubmed_url:
            return linked.pubmed_url
        return self.pubmed_url

    def get_cpd_article_id(self):
        linked = self.linked_article
        return linked.pk if linked else None


class LiveSessionSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return LiveSession.objects.filter(active=True).order_by("start_datetime")

    def lastmod(self, obj):
        return obj.modified
