from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CharField, EmailField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from spanza_journal_watch.users.managers import UserManager


class HealthService(models.Model):
    name = models.CharField(max_length=255, blank=False, null=False)
    url = models.URLField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    """
    Default custom user model for SPANZA Journal Watch.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore
    last_name = None  # type: ignore
    email = EmailField(_("email address"), unique=True)
    username = None  # type: ignore

    # Additional fields
    anonymous_author = models.BooleanField(default=False)
    health_services = models.ManyToManyField(HealthService, related_name="health_services")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.id})
