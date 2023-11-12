import datetime

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
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from spanza_journal_watch.utils.celerytasks import celery_resize_image
from spanza_journal_watch.utils.functions import estimate_reading_time, get_unique_slug, shorten_text
from spanza_journal_watch.utils.modelmethods import name_image
from spanza_journal_watch.utils.models import TimeStampedModel


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
            celery_resize_image.delay(self.logo.name, size=400)

    def __str__(self):
        return self.name


class Author(TimeStampedModel):
    title = models.CharField(max_length=255, default="Dr")
    name = models.CharField(max_length=255, blank=False, null=False)
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
            celery_resize_image.delay(self.profile_image.name, size=400)

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
    articles = models.ManyToManyField("Article", related_name="tags")

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
            print(f"Deleting unused tag {self}")
            self.delete()


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


class Article(TimeStampedModel):
    TRUNCATED_NAME_LENGTH = 50

    _original_tags_string = None  # Used to detect when tags_string has been changed on save()

    name = models.TextField()
    tags_string = models.TextField(blank=True, null=False, verbose_name="Add #hashtags that describe this article")
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, null=True, blank=True)
    year = models.IntegerField(
        validators=[MinValueValidator(1900), MaxValueValidator(datetime.date.today().year + 1)],
        default=datetime.date.today().year,
    )
    citation = models.TextField(null=True, blank=True)
    url = models.URLField(max_length=255, null=True, blank=True)
    active = models.BooleanField(default=False)

    class Meta:
        indexes = [GinIndex(name="gin_trgm_idx", fields=("name",), opclasses=("gin_trgm_ops",))]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_tags_string = self.tags_string

    def __str__(self):
        return self.get_truncated_name()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.tags_string != self._original_tags_string:
            current_tags = self.create_tag_objects()  # Creates new tags where necessary
            self.prune_tag_objects(current_tags)

    def get_related_review(self):
        return self.reviews.exclude(active=False).order_by("-created")[0]

    def get_truncated_name(self):
        return shorten_text(self.name, self.TRUNCATED_NAME_LENGTH)

    def get_title(self):
        separators = [":", " - "]
        for sep in separators:
            if sep in self.name:
                return self.name.split(sep, 1)[0].strip()
        return self.name

    def get_subtitle(self):
        separators = [":", "-"]
        for sep in separators:
            if sep in self.name:
                return self.name.split(sep, 1)[1].strip()
        return ""

    def tags_list(self):
        """
        Returns a list of unique 'hashtag' strings
        """
        hashtag_list = []
        for word in self.tags_string.split(" "):
            if slugify(word) and word[0] == "#":  # Ensure non-empty string after slugify
                hashtag_list.append(slugify(word[:255]))
        return list(set(hashtag_list))

    def create_tag_objects(self):
        """
        Creates new Tag objects from the self.tags_string where these
        do not already exist
        Returns a list of Tags matching the tags_string
        """
        current_tags = []

        for text in self.tags_list():
            try:
                tag = Tag.objects.get(text=text)
            except Tag.DoesNotExist:
                tag = Tag(text=text)
                tag.save()
            except Tag.MultipleObjectsReturned:
                print(f"Warning: multiple matching tags for {tag}")
                continue
            current_tags.append(tag)
            tag.articles.add(self)  # Will not duplicate relation, but triggers signals

        return current_tags

    def prune_tag_objects(self, current_tags):
        tags = self.tags.all()
        for tag in tags:
            if tag not in current_tags:
                tag.articles.remove(self)
                tag.delete_if_orphaned()


class Review(TimeStampedModel):
    TRUNCATED_BODY_LENGTH = 200
    MAX_LINE_CHARS = 50

    search_vector = SearchVectorField(null=True, blank=True)
    title_similarity = 0.1
    author_similarity = 0.3
    body_rank = 0.3

    article = models.ForeignKey(Article, on_delete=models.CASCADE, blank=False, null=False, related_name="reviews")
    slug = models.SlugField(max_length=50, null=False, blank=True, unique=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, blank=True, null=True, related_name="reviews")
    body = models.TextField()
    publish_date = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    feature_image = models.ImageField(
        upload_to=name_image,  # Handle path/name and delete old file
        blank=True,
        null=True,
    )

    class Meta:
        # Requires from django.contrib.postgres.operations import BtreeGinExtension in the migration
        indexes = [GinIndex(fields=("search_vector",))]

    def get_truncated_body(self):
        return shorten_text(self.body, self.TRUNCATED_BODY_LENGTH)

    def get_longer_truncated_body(self):
        return shorten_text(self.body, 500)

    def get_absolute_url(self):
        return reverse("submissions:review_detail", kwargs={"slug": self.slug})

    def get_reading_time(self):
        return estimate_reading_time(self.body)

    def get_full_name(self):
        return self.article.name

    def get_review_date(self):
        # Fall back to issue date if publish date not set
        return self.publish_date if self.publish_date else self.issues.all()[0].date

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
            celery_resize_image.delay(self.feature_image.name)

        # Create a SearchVector from the body text
        Review.objects.filter(pk=self.pk).update(search_vector=SearchVector("body"))

    @classmethod
    def search(cls, query):
        results = (
            cls.objects.exclude(active=False)
            .annotate(
                title_similarity=TrigramSimilarity("article__name", query),
                author_similarity=TrigramSimilarity("author__name", query),
                rank=SearchRank("search_vector", SearchQuery(query)),
            )
            .filter(
                Q(title_similarity__gt=cls.title_similarity)
                | Q(rank__gte=cls.body_rank)
                | Q(search_vector=SearchQuery(query))
                | Q(author_similarity__gt=cls.author_similarity)  # Exact matches
            )
            .annotate(headline=SearchHeadline("body", query, max_fragments=3, fragment_delimiter="...<br>..."))
            .order_by("-title_similarity", "-rank", "-author_similarity", "-created")
            .select_related("article", "author")
        )
        return results

    def __str__(self):
        return self.article.get_truncated_name()


class Issue(TimeStampedModel):
    name = models.CharField(max_length=255, null=False, blank=False)
    date = models.DateField(null=True, blank=True)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    body = models.TextField()
    reviews = models.ManyToManyField(Review, blank=True, related_name="issues")
    active = models.BooleanField(default=False)
    issue_detail_page = models.ForeignKey(
        to="layout.IssueDetailPage",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="issue_detail_page",
    )

    def get_card_features(self):
        features = []
        for review in self.reviews.all():
            if review.is_featured:
                features.append(review)
        return features

    def get_absolute_url(self):
        return reverse("submissions:issue_detail", kwargs={"slug": self.slug})

    def get_reading_time(self):
        return estimate_reading_time(self.body)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = get_unique_slug(self, slugify(self.name))
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Comment(TimeStampedModel):
    body = models.TextField()
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="comments")
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
