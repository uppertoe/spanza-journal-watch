from django.contrib.postgres.search import TrigramSimilarity
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


class ModelSearchMixin:
    """
    A mixin for models.Model instances that adds a search method
    Requires a models.BooleanField named 'active'
    Optionally sorted
    Returns a queryset of objects annotated by search rank
    """

    search_field = ""

    @classmethod
    def get_search_field(cls):
        return cls.search_field

    @classmethod
    def search(cls, search_query, sim_thres=0.3):
        search_results = (
            cls.objects.exclude(active=False)
            .annotate(
                similarity=TrigramSimilarity(cls.get_search_field(), search_query),
            )
            .filter(similarity__gt=sim_thres)
            .order_by("-similarity")
        )
        return search_results
