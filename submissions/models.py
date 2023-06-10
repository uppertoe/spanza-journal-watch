import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from spanza_journal_watch.utils.functions import unique_slugify
from spanza_journal_watch.utils.models import TimeStampedModel


class Tag(models.Model):
    text = models.CharField(max_length=255, unique=True, blank=False, null=False)
    slug = models.SlugField(max_length=255, blank=True, unique=True)
    active = models.BooleanField(default=True)
    articles = models.ManyToManyField("Article", related_name="tags")

    def __str__(self):
        return self.text

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, slugify(self.text))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("tag-detail", kwargs={"slug": self.slug})

    def all_tags_list():
        tags = (
            Tag.objects.exclude(active=False)
            .annotate(article_count=models.Count("articles"))
            .order_by("-article_count")
            .values_list("text", flat=True)
        )
        return [str(tag) for tag in tags]

    def delete_if_orphaned(self):
        if not self.articles.all().count():
            print(f"Deleting unused tag {self}")
            self.delete()


class Journal(TimeStampedModel):
    name = models.CharField(max_length=255, null=False, blank=False)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    abbreviation = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=255, null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, slugify(self.name))
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Article(TimeStampedModel):
    name = models.TextField()
    tags_string = models.TextField(blank=True, null=False, verbose_name="Add #hashtags that describe this article")
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, null=True, blank=True)
    year = models.IntegerField(
        validators=[MinValueValidator(1900), MaxValueValidator(datetime.date.today().year + 1)],
        default=datetime.date.today().year,
    )
    url = models.URLField(max_length=255, null=True, blank=True)

    _original_tags_string = None  # Used to detect when tags_string has been changed on save()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_tags_string = self.tags_string

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.tags_string != self._original_tags_string:
            current_tags = self.create_tag_objects()  # Creates new tags where necessary
            self.prune_tag_objects(current_tags)

    def shortened_name(self):
        length = 200
        if len(self.name) > length:
            return f"{self.name[:length]}..."
        return self.name

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
    article = models.ForeignKey(Article, on_delete=models.CASCADE, blank=False, null=False, related_name="reviews")
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True)
    body = models.TextField()
    active = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)

    def get_absolute_url(self):
        reverse("review-detail", kwargs={"slug": self.slug})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, slugify(self.article.name))
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Review: " + self.article.name


class Issue(TimeStampedModel):
    name = models.CharField(max_length=255, null=False, blank=False)
    date = models.DateField(null=True, blank=True)
    slug = models.SlugField(max_length=255, null=False, blank=True, unique=True)
    body = models.TextField()
    reviews = models.ManyToManyField(Review, blank=True, related_name="issues")
    active = models.BooleanField(default=False)
    main_feature = models.ForeignKey(
        to="layout.FeatureArticle",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="main_features",
        related_query_name="main_feature",
    )

    def get_card_features(self):
        features = []
        for review in self.reviews.all():
            if review.is_featured:
                features.append(review)
        return features

    def get_main_feature(self):
        return self.main_feature

    def get_absolute_url(self):
        reverse("issue-detail", kwargs={"slug": self.slug})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, slugify(self.name))
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

    @classmethod
    def update_page_count(cls, content_object):
        cls.objects.update_or_create(
            content_type=ContentType.objects.get_for_model(content_object),
            object_id=content_object.id,
            defaults={"count": models.F("count") + 1, "last_accessed": timezone.now()},
        )

    @classmethod
    def get_count(cls, content_object):
        page_count, _ = cls.objects.get_or_create(
            content_type=ContentType.objects.get_for_model(content_object), object_id=content_object.id
        )
        return page_count.count
