"""
With these settings, tests run faster.
"""

from .base import *  # noqa
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="I7QJp4WddBlMv3zpbUEfL8Qg072aEHV7ovVC3RjI85xJcvH55DY1jt4KP5grVUg8",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"
DEBUG = True

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore # noqa: F405

# django-webpack-loader
# ------------------------------------------------------------------------------
WEBPACK_LOADER["DEFAULT"]["LOADER_CLASS"] = "webpack_loader.loaders.FakeWebpackLoader"  # noqa: F405
# Integrations
# ------------------------------------------------------------------------------
# Tests must not depend on the developer's .env.local or reach a Planka, PubMed or
# Anthropic endpoint: every integration is unconfigured here, and the snapshot suite
# records pages in that state. Tests that need a value use override_settings.
PLANKA_BASE_URL = ""
PLANKA_EXTERNAL_URL = ""
PLANKA_CALLBACK_BASE_URL = ""
PLANKA_API_KEY = ""
PLANKA_ACCESS_TOKEN = ""
PLANKA_WEBHOOK_SECRET = ""
PLANKA_ADMIN_EMAIL = ""
PLANKA_DB_URL = ""
ANTHROPIC_API_KEY = ""
INDEXNOW_KEY = ""
OAUTH2_PROVIDER = {**OAUTH2_PROVIDER, "OIDC_ISS_ENDPOINT": None}  # noqa: F405
# Your stuff...
# ------------------------------------------------------------------------------
