from django.db import models


class TimeStampedModel(models.Model):
    """
    An abstract base class model that provides selfupdating
    ``created`` and ``modified`` fields.
    """

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PageModel(models.Model):
    """
    An abstract base class model that
    provides fields for layout Page models
    """

    feature_article = models.ForeignKey("FeatureArticle", on_delete=models.CASCADE)
    overlay_light = models.TextField(blank=True, null=True)
    overlay_dark = models.TextField(blank=True, null=True)
    additional_css = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    @classmethod
    def get_latest_instance(cls):
        return cls.objects.exclude(active=False).order_by("-modified").first()

    def __str__(self):
        return str(self.feature_article)
