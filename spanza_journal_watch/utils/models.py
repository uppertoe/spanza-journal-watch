from django.db import models
from django.forms import model_to_dict


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

    def collate_fields(self, **kwargs):
        """
        Outputs a dictionary with fields from a PageModel and
        its ForeignKey relations in a single level

        Updates with kwargs from the view if fields need to be replaced

        Suitable for including as context in a View
        """

        fields_dict = model_to_dict(self)

        # Include immediate fields of the foreign key model
        foreign_key_fields = [field.name for field in self._meta.fields if isinstance(field, models.ForeignKey)]
        for field_name in foreign_key_fields:
            field_value = getattr(self, field_name)
            if field_value:
                foreign_key_fields_dict = model_to_dict(field_value)
                # Updates (overwrites) dict with values from the ForeignKey model
                fields_dict.update({key: value for key, value in foreign_key_fields_dict.items()})

        # Add replacement fields from the View
        for field, value in kwargs.items():
            fields_dict.update({field: value})

        return fields_dict

    def __str__(self):
        return str(self.feature_article)
