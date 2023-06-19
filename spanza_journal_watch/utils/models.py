from django.contrib.postgres.search import SearchRank, SearchVector
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
    def search(cls, search_query, sort=False, rank=0.3):
        search_vector = cls.get_search_vector()
        search_results = cls.objects.annotate(rank=SearchRank(search_vector, search_query)).filter(rank__gte=rank)
        if sort:
            search_results = search_results.order_by("-rank")
        return search_results
