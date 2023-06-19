from django.contrib.postgres.search import SearchRank, SearchVector, TrigramSimilarity
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

    search_fields = []

    @classmethod
    def get_search_vector(cls):
        search_vector = None
        for field, weight in cls.search_fields:
            if search_vector is None:
                search_vector = SearchVector(field, weight=weight)
            else:
                search_vector += SearchVector(field, weight=weight)
        return search_vector

    @classmethod
    def search(cls, search_query, sort=True, rank=0.3, similarity=0.3):
        search_vector = cls.get_search_vector()
        search_results = (
            cls.objects.exclude(active=False)
            .annotate(similarity=TrigramSimilarity(*cls.search_fields, search_query))
            .annotate(rank=SearchRank(search_vector, search_query))
            .filter(rank__gte=rank, similarity__gte=similarity)
        )
        if sort:
            search_results = search_results.order_by(models.F("rank").desc())
        return search_results
