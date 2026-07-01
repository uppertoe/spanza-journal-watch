import logging
import re

import bleach
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
    SearchVector,
    SearchVectorField,
    TrigramSimilarity,
)
from django.core.cache import cache
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from markdownx.models import MarkdownxField
from markdownx.utils import markdownify

from spanza_journal_watch.utils.cache import get_content_cache_version
from spanza_journal_watch.utils.celerytasks import celery_resize_image
from spanza_journal_watch.utils.functions import estimate_reading_time, get_unique_slug, shorten_text
from spanza_journal_watch.utils.modelmethods import name_image
from spanza_journal_watch.utils.models import TimeStampedModel

logger = logging.getLogger(__name__)

SAFE_MARKDOWN_TAGS = sorted(
    set(bleach.sanitizer.ALLOWED_TAGS).union(
        {
            "p",
            "br",
            "hr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "pre",
            "code",
            "blockquote",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
        }
    )
)
SAFE_MARKDOWN_ATTRIBUTES = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title"],
    "th": ["colspan", "rowspan", "scope"],
    "td": ["colspan", "rowspan"],
}
SAFE_MARKDOWN_PROTOCOLS = set(bleach.sanitizer.ALLOWED_PROTOCOLS).union({"mailto"})


def sanitize_markdown_html(html):
    return bleach.clean(
        html or "",
        tags=SAFE_MARKDOWN_TAGS,
        attributes=SAFE_MARKDOWN_ATTRIBUTES,
        protocols=SAFE_MARKDOWN_PROTOCOLS,
        strip=True,
    )


class HealthService(models.Model):
    name = models.CharField(max_length=255, blank=False, null=False)
    url = models.URLField(max_length=255, blank=True, null=True)
    logo = models.ImageField(
        upload_to=name_image,  # Handle path/name and delete old file
        blank=True,
        null=True,
    )
    logo_authorised = models.BooleanField(default=False)

    def is_logo_authorised(self):
        return self.logo and self.logo_authorised

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.logo:
            celery_resize_image.delay(
                "submissions.HealthService",
                self.pk,
                "logo",
                size=400,
                target_format="webp",
                variant_widths=(200,),
            )

    def __str__(self):
        return self.name


class Author(TimeStampedModel):
    title = models.CharField(max_length=255, default="Dr")
    name = models.CharField(max_length=255, blank=False, null=False)
    email = models.EmailField(
        blank=True, null=True, unique=True, help_text="Email used to match this author to invited contributors"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True)
    anonymous = models.BooleanField(default=False)
    health_services = models.ManyToManyField(HealthService, blank=True, related_name="authors")
    slug = models.SlugField(max_length=255, blank=True)
    profile_image = models.ImageField(
        upload_to=name_image,  # Handle path/name and delete old file
        blank=True,
        null=True,
    )
    show_profile_image = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.name))
        super().save(*args, **kwargs)

        if self.profile_image:
            celery_resize_image.delay(
                "submissions.Author",
                self.pk,
                "profile_image",
                size=400,
                target_format="webp",
                variant_widths=(200,),
            )

    def is_profile_image(self):
        return self.profile_image and self.show_profile_image

    def get_review_count(self):
        return Review.objects.filter(active=True, author=self).count()

    def get_absolute_url(self):
        return reverse("submissions:author_detail", kwargs={"slug": self.slug})

    def __str__(self):
        return " ".join([self.title, self.name]) if self.title else self.name


class Tag(models.Model):
    text = models.CharField(max_length=255, unique=True, blank=False, null=False)
    slug = models.SlugField(max_length=255, blank=True, unique=True)
    active = models.BooleanField(default=True)
    curated = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    articles = models.ManyToManyField("backend.PubmedArticle", related_name="tags")

    class Meta:
        indexes = [
            models.Index(fields=["active", "curated"]),
        ]

    @classmethod
    def get_all_tags(cls):
        tags = (
            cls.objects.exclude(active=False)
            .annotate(article_count=models.Count("articles"))
            .order_by("-article_count")
            .values_list("text", flat=True)
        )
        return tags

    def __str__(self):
        return f"#{self.text}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.text))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("submissions:tag_detail", kwargs={"slug": self.slug})

    def delete_if_orphaned(self):
        if not self.articles.all().count():
            logger.debug("Deleting unused tag %s", self)
            self.delete()


class MeshTagMapping(models.Model):
    mesh_term = models.CharField(max_length=255, unique=True, db_index=True)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="mesh_mappings")

    class Meta:
        ordering = ("mesh_term",)

    def __str__(self):
        return f"{self.mesh_term} → {self.tag.text}"


class CuratedCollection(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    tags = models.ManyToManyField(Tag, related_name="collections", blank=True)
    reviews = models.ManyToManyField("Review", related_name="collections", blank=True)
    active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("display_order", "-created")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.title))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("submissions:collection_detail", kwargs={"slug": self.slug})


class Journal(TimeStampedModel):
    name = models.CharField(max_length=255, null=False, blank=False)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    abbreviation = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=255, null=True, blank=True)
    active = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.name))
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Review(TimeStampedModel):
    TRUNCATED_BODY_LENGTH = 200
    MAX_LINE_CHARS = 50

    search_vector = SearchVectorField(null=True, blank=True)

    article = models.ForeignKey("backend.PubmedArticle", on_delete=models.CASCADE, related_name="reviews")
    slug = models.SlugField(max_length=50, null=False, blank=True, unique=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, blank=True, null=True, related_name="reviews")
    body = MarkdownxField()
    publish_date = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    feature_image = models.ImageField(
        upload_to=name_image,  # Handle path/name and delete old file
        blank=True,
        null=True,
    )
    heading_tag_re = re.compile(r"<h[1-6]\b[^>]*>.*?</h[1-6]>", re.IGNORECASE | re.DOTALL)

    class Meta:
        # Requires from django.contrib.postgres.operations import BtreeGinExtension in the migration
        indexes = [
            GinIndex(fields=("search_vector",)),
            models.Index(fields=["active", "-created"]),
            models.Index(fields=["active", "-publish_date"], name="submissions_active_91ec4a_idx"),
        ]

    def _render_markdown_body_html(self):
        if not hasattr(self, "_markdown_html_cache"):
            self._markdown_html_cache = sanitize_markdown_html(markdownify(self.body))
        return self._markdown_html_cache

    def get_markdown_body(self, strip=False):
        html = self._render_markdown_body_html()
        return html if not strip else strip_tags(html)

    def get_plain_body(self, exclude_headings=False):
        html = self._render_markdown_body_html()
        if exclude_headings:
            html = self.heading_tag_re.sub(" ", html)
        text = strip_tags(html).strip()
        return text

    def get_truncated_body(self):
        return shorten_text(self.get_plain_body(exclude_headings=True), self.TRUNCATED_BODY_LENGTH)

    def get_longer_truncated_plain_body(self):
        return shorten_text(self.get_plain_body(), 500)

    def get_absolute_url(self):
        return reverse("submissions:review_detail", kwargs={"slug": self.slug})

    def get_reading_time(self):
        return estimate_reading_time(self.body)

    def get_full_name(self):
        return self.article.name

    def get_review_date(self):
        # Fall back to issue date if publish date not set
        return self.publish_date if self.publish_date else self.issues.all()[0].date

    def get_hits(self):
        from spanza_journal_watch.analytics.models import AnalyticsEvent

        object_id = self.id
        content_type = ContentType.objects.get_for_model(self)

        human_open_events = AnalyticsEvent.objects.filter(
            content_type=content_type,
            object_id=object_id,
            event_type=AnalyticsEvent.EventType.REVIEW_OPEN,
            automated=False,
        )
        if human_open_events.exists():
            distinct_session_views = human_open_events.exclude(session_key="").values("session_key").distinct().count()
            sessionless_views = human_open_events.filter(session_key="").count()
            return distinct_session_views + sessionless_views

        try:
            hit = Hit.objects.get(
                content_type=content_type,
                object_id=object_id,
            )
            return hit.count
        except Hit.DoesNotExist:
            return 0

    def save(self, *args, **kwargs):
        # Create the slug if it doesn't exist
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.article.name))

        # Perform an initial save
        super().save(*args, **kwargs)

        if not self.publish_date and self.active:
            Review.objects.filter(pk=self.pk).update(publish_date=self.created)

        # Delegate resizing to Celery
        if self.feature_image:
            celery_resize_image.delay(
                "submissions.Review",
                self.pk,
                "feature_image",
                target_format="original",
                variant_widths=(240, 480),
            )

        # Update the body-only search vector
        Review.objects.filter(pk=self.pk).update(search_vector=SearchVector("body"))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    # Thresholds — class-level so they can be tuned in one place
    TITLE_TRIGRAM_THRESHOLD = 0.15
    AUTHOR_TRIGRAM_THRESHOLD = 0.3
    JOURNAL_TRIGRAM_THRESHOLD = 0.35
    BODY_RANK_THRESHOLD = 0.05
    HEADLINE_DELIMITER = "JWFRAGDELIM"

    @classmethod
    def search(cls, query):
        """Search reviews using trigram similarity for names and full-text search for body content.

        Tag matching is done as a separate ID lookup to avoid a join that
        multiplies rows and makes the trigram/FTS filter much more expensive.
        """
        search_query = SearchQuery(query)

        # Collect review IDs that match via tags (separate query avoids row multiplication)
        tag_match_ids = set(
            cls.objects.filter(active=True, article__tags__text__icontains=query).values_list("id", flat=True)
        )

        # Collect review IDs whose author name contains the query. Whole-string
        # trigram similarity is diluted by extra words in multi-part names (e.g.
        # "barney" vs "Barney Rathnayaka Mudiyanselage" scores below threshold),
        # so a substring match guarantees name lookups resolve.
        author_match_ids = set(
            cls.objects.filter(active=True, author__name__icontains=query).values_list("id", flat=True)
        )

        # Main query: trigram + full-text on the base review table (no tag join)
        results = (
            cls.objects.exclude(active=False)
            .annotate(
                title_similarity=TrigramSimilarity("article__title", query),
                author_similarity=TrigramSimilarity("author__name", query),
                journal_similarity=TrigramSimilarity("article__journal__name", query),
                body_rank=SearchRank("search_vector", search_query),
            )
            .filter(
                Q(title_similarity__gte=cls.TITLE_TRIGRAM_THRESHOLD)
                | Q(body_rank__gte=cls.BODY_RANK_THRESHOLD)
                | Q(search_vector=search_query)
                | Q(author_similarity__gte=cls.AUTHOR_TRIGRAM_THRESHOLD)
                | Q(journal_similarity__gte=cls.JOURNAL_TRIGRAM_THRESHOLD)
                | Q(pk__in=tag_match_ids)
                | Q(pk__in=author_match_ids)
            )
            .annotate(
                headline=SearchHeadline(
                    "body", search_query, max_fragments=3, fragment_delimiter=cls.HEADLINE_DELIMITER
                ),
            )
            .order_by("-title_similarity", "-body_rank", "-author_similarity", "-created")
            .select_related("article__journal", "author")
            .prefetch_related("article__tags", "issues")
        )

        return results

    @classmethod
    def post_process_headlines(cls, results):
        """Convert search headline fragments to clean text with <mark> highlights.

        If the headline contains no highlighted terms (no <b> tags from
        SearchHeadline), clear it so the template falls back to the
        truncated body instead of showing a random excerpt.
        """
        for r in results:
            raw = r.headline or ""
            if "<b>" not in raw:
                r.headline = ""
                continue
            # Protect search highlights by converting to placeholder
            text = raw.replace("<b>", "\x00MARK\x00").replace("</b>", "\x00/MARK\x00")
            # Convert markdown to HTML, then strip all tags (except our placeholders)
            html = markdownify(text)
            html = cls.heading_tag_re.sub(" ", html)
            text = strip_tags(html)
            # Clean up whitespace and markdown artefacts
            text = re.sub(r"(?m)(^|\n)\s{0,3}#{1,6}\s*", " ", text)
            text = text.replace(cls.HEADLINE_DELIMITER, " &hellip; ")
            text = re.sub(r"\s+", " ", text).strip()
            # Restore highlights as <mark> tags
            text = text.replace("\x00MARK\x00", "<mark>").replace("\x00/MARK\x00", "</mark>")
            r.headline = text

    def __str__(self):
        return self.article.get_truncated_name()


class Issue(TimeStampedModel):
    name = models.CharField(max_length=255, null=False, blank=False)
    date = models.DateField(null=True, blank=True)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    body = models.TextField()
    image = models.ImageField(upload_to="issues/", blank=True, null=True)
    reviews = models.ManyToManyField(Review, blank=True, related_name="issues")
    active = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["active", "-date"]),
        ]
        permissions = [
            ("manage_issue_builder", "Can create and publish issue bundles in backend issue builder"),
            ("chief_editor", "Can edit reviews, publish issues, and access chief editor functions"),
            ("regional_coordinator", "Can edit assigned issues and reviews; cannot publish or manage newsletter"),
            ("can_recommend", "Can recommend articles for review"),
            ("invited_contributor", "Invited contributor — can access editorial tools and Planka"),
        ]

    def get_card_features(self):
        features = []
        for review in self.reviews.all():
            if review.is_featured:
                features.append(review)
        return features

    @classmethod
    def attach_display_images(cls, issues):
        issue_list = list(issues)
        if not issue_list:
            return issue_list

        unresolved = [issue for issue in issue_list if not issue.image]
        if not unresolved:
            for issue in issue_list:
                issue.display_issue_image = issue.image
            return issue_list

        PageHeader = apps.get_model("layout", "PageHeader")
        headers = list(
            PageHeader.objects.filter(page_type=PageHeader.PageType.ISSUE_DETAIL, active=True)
            .select_related("feature_article")
            .order_by("-modified")
        )

        def match_feature_article(issue):
            issue_slug = (issue.slug or "").strip().lower()
            issue_name = (issue.name or "").strip().lower()

            if issue_slug:
                for header in headers:
                    feature_slug = (header.feature_article.slug or "").strip().lower()
                    if feature_slug == issue_slug:
                        return header.feature_article
                for header in headers:
                    feature_slug = (header.feature_article.slug or "").strip().lower()
                    if feature_slug.startswith(issue_slug):
                        return header.feature_article

            if issue_name:
                for header in headers:
                    feature_title = (header.feature_article.title or "").strip().lower()
                    if feature_title == issue_name:
                        return header.feature_article
                for header in headers:
                    feature_title = (header.feature_article.title or "").strip().lower()
                    if feature_title.startswith(issue_name):
                        return header.feature_article

            if issue.date:
                month_year = issue.date.strftime("%B %Y").lower()
                for header in headers:
                    feature_title = (header.feature_article.title or "").strip().lower()
                    if month_year in feature_title:
                        return header.feature_article

            return None

        for issue in issue_list:
            if issue.image:
                issue.display_issue_image = issue.image
                continue

            feature_article = match_feature_article(issue)
            issue.display_issue_image = feature_article.image if feature_article and feature_article.image else None

        return issue_list

    def get_absolute_url(self):
        return reverse("submissions:issue_detail", kwargs={"slug": self.slug})

    def get_reading_time(self):
        return estimate_reading_time(self.body)

    def get_header_feature_article(self):
        PageHeader = apps.get_model("layout", "PageHeader")
        cache_version = get_content_cache_version()
        issue_key = self.slug or f"pk-{self.pk}"
        cache_key = f"issue:header_feature_article:v{cache_version}:{issue_key}"

        def resolve_feature_article():
            headers = PageHeader.objects.filter(
                page_type=PageHeader.PageType.ISSUE_DETAIL, active=True
            ).select_related("feature_article")

            issue_slug = (self.slug or "").strip()
            issue_name = (self.name or "").strip()
            header = None

            if issue_slug:
                header = headers.filter(feature_article__slug__iexact=issue_slug).order_by("-modified").first()
                if not header:
                    header = (
                        headers.filter(feature_article__slug__istartswith=issue_slug).order_by("-modified").first()
                    )

            if not header and issue_name:
                header = headers.filter(feature_article__title__iexact=issue_name).order_by("-modified").first()
                if not header:
                    header = (
                        headers.filter(feature_article__title__istartswith=issue_name).order_by("-modified").first()
                    )

            if not header and self.date:
                month_year = self.date.strftime("%B %Y")
                header = headers.filter(feature_article__title__icontains=month_year).order_by("-modified").first()

            return header.feature_article if header else None

        return cache.get_or_set(cache_key, resolve_feature_article, timeout=60 * 30)

    def get_issue_image(self):
        cached_image = getattr(self, "display_issue_image", None)
        if cached_image is not None:
            return cached_image
        if self.image:
            return self.image
        feature_article = self.get_header_feature_article()
        if feature_article and feature_article.image:
            return feature_article.image
        return None

    def save(self, *args, **kwargs):
        retired_slug = None
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.name))
        elif self.pk:
            # Re-slug when the name changes so the URL tracks the issue name,
            # and remember the old slug so its links keep resolving.
            previous = type(self).objects.filter(pk=self.pk).values_list("name", "slug").first()
            if previous:
                previous_name, previous_slug = previous
                desired_base = slugify(self.name)
                if previous_name != self.name and desired_base:
                    desired_slug = get_unique_slug(self, desired_base, exclude_pk=self.pk)
                    if desired_slug and desired_slug != self.slug:
                        retired_slug = self.slug
                        self.slug = desired_slug
        super().save(*args, **kwargs)

        if retired_slug and retired_slug != self.slug:
            # The new slug can never also be a stale redirect for another issue.
            IssueSlugRedirect.objects.filter(old_slug=self.slug).delete()
            IssueSlugRedirect.objects.update_or_create(old_slug=retired_slug, defaults={"issue": self})

        if self.image:
            celery_resize_image.delay(
                "submissions.Issue",
                self.pk,
                "image",
                size=800,
                target_format="webp",
                variant_widths=(240, 480),
            )

        return None

    def __str__(self):
        return self.name


class IssueSlugRedirect(TimeStampedModel):
    """Maps a retired Issue slug to its current Issue so old links 301 forward."""

    old_slug = models.SlugField(max_length=255, unique=True)
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="slug_redirects")

    class Meta:
        verbose_name = "Issue slug redirect"

    def __str__(self):
        return f"{self.old_slug} → {self.issue.slug}"


class Comment(TimeStampedModel):
    body = models.TextField()
    article = models.ForeignKey("backend.PubmedArticle", on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return self.body


class Hit(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    count = models.PositiveIntegerField(default=0)
    last_accessed = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("content_type", "object_id")
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]
        ordering = ("-count",)

    @classmethod
    def update_page_count(cls, content_object):
        # Implement 'get_or_create' to allow for F() operations
        content_type = ContentType.objects.get_for_model(content_object)
        id = content_object.id
        try:
            hit = cls.objects.get(content_type=content_type, object_id=id)
        except cls.DoesNotExist:
            hit = cls(content_type=content_type, object_id=id)
            hit.save()
        hit.count = models.F("count") + 1  # database-side operation
        hit.last_accessed = timezone.now()
        hit.save(update_fields=["count", "last_accessed"])  # avoid race condition

    @classmethod
    def get_count(cls, content_object):
        page_count, _ = cls.objects.get_or_create(
            content_type=ContentType.objects.get_for_model(content_object), object_id=content_object.id
        )
        return page_count.count

    def __str__(self):
        return f"Hitcount: {str(self.content_object)}"
