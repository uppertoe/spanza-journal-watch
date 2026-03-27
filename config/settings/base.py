"""
Base settings to build other settings files upon.
"""

from pathlib import Path

import environ
from django.contrib.messages import constants as messages

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent
# spanza_journal_watch/
APPS_DIR = BASE_DIR / "spanza_journal_watch"
LOGS_DIR = BASE_DIR / "access_logs"
LOG_FILE = LOGS_DIR / "access_logs.log"
env = environ.Env()

READ_DOT_ENV_FILE = env.bool("DJANGO_READ_DOT_ENV_FILE", default=False)
if READ_DOT_ENV_FILE:
    # OS environment variables take precedence over variables from .env
    env.read_env(str(BASE_DIR / ".env"))

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = env.bool("DJANGO_DEBUG", False)
# Local time zone. Choices are
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# though not all of them may be available with every OS.
# In Windows, this must be set to your system time zone.
TIME_ZONE = "Australia/Melbourne"
# https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = "en-us"
# https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1
# https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = True
# https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True
# https://docs.djangoproject.com/en/dev/ref/settings/#locale-paths
LOCALE_PATHS = [str(BASE_DIR / "locale")]

# MESSAGES
# ------------------------------------------------------------------------------
# Convert Django messages to Bootstrap alert styles
MESSAGE_TAGS = {
    messages.DEBUG: "alert-secondary",
    messages.INFO: "alert-info",
    messages.SUCCESS: "alert-success",
    messages.WARNING: "alert-warning",
    messages.ERROR: "alert-danger",
}

# DATABASES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#databases
DATABASE_URL = env("DATABASE_URL", default="")
if not DATABASE_URL:
    postgres_name = env("POSTGRES_DB", default="")
    postgres_user = env("POSTGRES_USER", default="")
    postgres_password = env("POSTGRES_PASSWORD", default="")
    postgres_host = env("POSTGRES_HOST", default="")
    postgres_port = env("POSTGRES_PORT", default="5432")

    if all([postgres_name, postgres_user, postgres_password, postgres_host]):
        DATABASE_URL = (
            f"postgres://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_name}"
        )

DATABASES = {"default": env.db("DATABASE_URL", default=DATABASE_URL)}
DATABASES["default"]["ATOMIC_REQUESTS"] = env.bool("DJANGO_ATOMIC_REQUESTS", default=False)
# https://docs.djangoproject.com/en/stable/ref/settings/#std:setting-DEFAULT_AUTO_FIELD
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# URLS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "config.urls"
# https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "config.wsgi.application"

# APPS
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # "django.contrib.humanize", # Handy template tags
    "django.contrib.admin",
    "django.forms",
    "django.contrib.sitemaps",
]
THIRD_PARTY_APPS = [
    "crispy_forms",
    "crispy_bootstrap5",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "oauth2_provider",
    "django_celery_beat",
    "webpack_loader",
    "view_breadcrumbs",
    "tinymce",
    "mjml",
    "markdownx",
]

LOCAL_APPS = [
    "spanza_journal_watch.users",
    "spanza_journal_watch.submissions.apps.SubmissionsConfig",
    "spanza_journal_watch.layout.apps.LayoutConfig",
    "spanza_journal_watch.newsletter.apps.NewsletterConfig",
    "spanza_journal_watch.analytics.apps.AnalyticsConfig",
    "spanza_journal_watch.backend.apps.BackendConfig",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# MIGRATIONS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#migration-modules
MIGRATION_MODULES = {"sites": "spanza_journal_watch.contrib.sites.migrations"}

# AUTHENTICATION
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#authentication-backends
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
    "oauth2_provider.backends.OAuth2Backend",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-user-model
AUTH_USER_MODEL = "users.User"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-redirect-url
LOGIN_REDIRECT_URL = "users:redirect"
# https://docs.djangoproject.com/en/dev/ref/settings/#login-url
LOGIN_URL = "account_login"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = [
    # https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# MIDDLEWARE
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "request_logging.middleware.LoggingMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "spanza_journal_watch.backend.middleware.HtmxMessagesMiddleware",
]

# STATIC
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(BASE_DIR / "staticfiles")
# https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = "/static/"
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = [str(APPS_DIR / "static")]
# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = str(APPS_DIR / "media")
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "/media/"

# TEMPLATES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # https://docs.djangoproject.com/en/dev/ref/settings/#dirs
        "DIRS": [str(APPS_DIR / "templates")],
        # https://docs.djangoproject.com/en/dev/ref/settings/#app-dirs
        "APP_DIRS": True,
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.template.context_processors.static",
                "django.template.context_processors.tz",
                "django.contrib.messages.context_processors.messages",
                "spanza_journal_watch.users.context_processors.allauth_settings",
                "spanza_journal_watch.utils.context_processors.content_cache_version",
                "spanza_journal_watch.backend.context_processors.selected_issue",
            ],
            "builtins": [
                "spanza_journal_watch.submissions.templatetags.wrapchars",  # Add your app's templatetags module here
            ],
        },
    }
]

# https://docs.djangoproject.com/en/dev/ref/settings/#form-renderer
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# http://django-crispy-forms.readthedocs.io/en/latest/install.html#template-packs
CRISPY_TEMPLATE_PACK = "bootstrap5"
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"

# FIXTURES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#fixture-dirs
FIXTURE_DIRS = (str(APPS_DIR / "fixtures"),)

# SECURITY
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-httponly
SESSION_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-httponly
CSRF_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#x-frame-options
X_FRAME_OPTIONS = "DENY"
# https://docs.djangoproject.com/en/dev/ref/middleware/#x-content-type-options-nosniff
SECURE_CONTENT_TYPE_NOSNIFF = True

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#email-timeout
EMAIL_TIMEOUT = 5

# MJML
# ------------------------------------------------------------------------------
MJML_BACKEND_MODE = "tcpserver"
MJML_TCPSERVERS = [
    ("mjml", 28101),  # the host and port of MJML TCP-Server
]

# FILE UPLOAD
# ------------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", default=10_000_000)

# ISSUE BUILDER
# ------------------------------------------------------------------------------
ISSUE_BUILDER_MAX_FEATURED_REVIEWS = env.int("ISSUE_BUILDER_MAX_FEATURED_REVIEWS", default=2)
ISSUE_CONTRIBUTOR_INVITE_TTL_DAYS = env.int("ISSUE_CONTRIBUTOR_INVITE_TTL_DAYS", default=180)

# PLANKA
# ------------------------------------------------------------------------------
PLANKA_BASE_URL = env("PLANKA_BASE_URL", default="")
PLANKA_API_KEY = env("PLANKA_API_KEY", default="")
PLANKA_ACCESS_TOKEN = env("PLANKA_ACCESS_TOKEN", default="")
PLANKA_TIMEOUT_SECONDS = env.int("PLANKA_TIMEOUT_SECONDS", default=15)
PLANKA_CREDENTIAL_ENCRYPTION_KEY = env("PLANKA_CREDENTIAL_ENCRYPTION_KEY", default="")
# Admin email used by setup_planka_api_key management command.
# Must match DEFAULT_ADMIN_EMAIL in the Planka container env.
PLANKA_ADMIN_EMAIL = env("PLANKA_ADMIN_EMAIL", default="")
# Base URL that Planka can reach Django at (used when registering webhooks).
# In dev this is typically http://django:8000; in prod it's your public domain.
PLANKA_CALLBACK_BASE_URL = env("PLANKA_CALLBACK_BASE_URL", default="")
# Shared secret verified on incoming webhook requests from Planka.
PLANKA_WEBHOOK_SECRET = env("PLANKA_WEBHOOK_SECRET", default="")
# Direct connection to Planka's own Postgres, used only by setup_planka_api_key.
PLANKA_DB_URL = env("PLANKA_DB_URL", default="")

# PUBMED
# ------------------------------------------------------------------------------
PUBMED_TIMEOUT_SECONDS = env.int("PUBMED_TIMEOUT_SECONDS", default=20)
PUBMED_CREDENTIAL_ENCRYPTION_KEY = env("PUBMED_CREDENTIAL_ENCRYPTION_KEY", default="")

# ADMIN
# ------------------------------------------------------------------------------
# Django Admin URL.
ADMIN_URL = "admin/"
# https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = ["eamonn.upperton@gmail.com"]
# https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS

# LOGGING
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#logging
# See https://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
}

# Celery
# ------------------------------------------------------------------------------
if USE_TZ:
    # https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-timezone
    CELERY_TIMEZONE = TIME_ZONE
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-broker_url
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=env("REDIS_URL", default="redis://redis:6379/0"))
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_backend
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-extended
CELERY_RESULT_EXTENDED = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-always-retry
# https://github.com/celery/celery/pull/6122
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#result-backend-max-retries
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-accept_content
CELERY_ACCEPT_CONTENT = ["json"]
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-task_serializer
CELERY_TASK_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std:setting-result_serializer
CELERY_RESULT_SERIALIZER = "json"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-time-limit
CELERY_TASK_TIME_LIMIT = 10 * 60  # 10 min hard kill (covers long PubMed fetches)
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-soft-time-limit
CELERY_TASK_SOFT_TIME_LIMIT = 8 * 60  # 8 min soft limit — task can clean up gracefully
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#beat-scheduler
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-send-task-events
CELERY_WORKER_SEND_TASK_EVENTS = True
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_send_sent_event
CELERY_TASK_SEND_SENT_EVENT = True
# django-allauth
# ------------------------------------------------------------------------------
ACCOUNT_ALLOW_REGISTRATION = env.bool("DJANGO_ACCOUNT_ALLOW_REGISTRATION", False)
# https://django-allauth.readthedocs.io/en/latest/configuration.html
ACCOUNT_LOGIN_METHODS = {"email"}
# https://django-allauth.readthedocs.io/en/latest/configuration.html
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
# https://django-allauth.readthedocs.io/en/latest/configuration.html
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
# https://django-allauth.readthedocs.io/en/latest/configuration.html
# "optional": users can log in without verifying — appropriate for an invite-based internal tool
# where the invite link already proves email ownership.
ACCOUNT_EMAIL_VERIFICATION = "optional"
# https://django-allauth.readthedocs.io/en/latest/configuration.html
ACCOUNT_ADAPTER = "spanza_journal_watch.users.adapters.AccountAdapter"
# https://django-allauth.readthedocs.io/en/latest/forms.html
ACCOUNT_FORMS = {"signup": "spanza_journal_watch.users.forms.UserSignupForm"}
# https://django-allauth.readthedocs.io/en/latest/configuration.html
SOCIALACCOUNT_ADAPTER = "spanza_journal_watch.users.adapters.SocialAccountAdapter"
# https://django-allauth.readthedocs.io/en/latest/forms.html
SOCIALACCOUNT_FORMS = {"signup": "spanza_journal_watch.users.forms.UserSocialSignupForm"}

# django-oauth-toolkit — Django acts as the OIDC provider for Planka
# ------------------------------------------------------------------------------
import base64  # noqa: E402

_oidc_key_b64 = env("OIDC_RSA_PRIVATE_KEY", default="")

_oidc_iss_endpoint = env("OIDC_ISS_ENDPOINT", default="")

OAUTH2_PROVIDER = {
    "OIDC_ENABLED": True,
    "OIDC_RSA_PRIVATE_KEY": base64.b64decode(_oidc_key_b64).decode() if _oidc_key_b64 else "",
    "OIDC_ISS_ENDPOINT": _oidc_iss_endpoint or None,
    "OAUTH2_VALIDATOR_CLASS": "spanza_journal_watch.backend.oidc_validator.OIDCValidator",
    "SCOPES": {
        "openid": "OpenID Connect scope",
        "email": "Email address",
        "profile": "Profile information",
    },
    "PKCE_REQUIRED": False,
}

PLANKA_EXTERNAL_URL = env("PLANKA_EXTERNAL_URL", default="")


# django-webpack-loader
# ------------------------------------------------------------------------------
WEBPACK_LOADER = {
    "DEFAULT": {
        "CACHE": not DEBUG,
        "STATS_FILE": BASE_DIR / "webpack-stats.json",
        "POLL_INTERVAL": 0.1,
        "IGNORE": [r".+\.hot-update.js", r".+\.map"],
    }
}
# Your stuff...
# ------------------------------------------------------------------------------

# MarkdownX
MARKDOWNX_MARKDOWN_EXTENSIONS = [
    "markdown.extensions.extra",  # tables, footnotes, etc.
    "markdown.extensions.sane_lists",  # if you like smarter lists
    # <-- NO 'nl2br' here!
]

# tiny-MCE
# ------------------------------------------------------------------------------
TINYMCE_DEFAULT_CONFIG = {
    "theme": "silver",
    "height": 500,
    "menubar": False,
    "plugins": "advlist,autolink,lists,link,image,charmap,print,preview,anchor,"
    "searchreplace,visualblocks,code,fullscreen,insertdatetime,media,table,paste,"
    "code,help,wordcount",
    "toolbar": "undo redo | formatselect | "
    "bold italic backcolor | alignleft aligncenter "
    "alignright alignjustify | bullist numlist outdent indent | "
    "removeformat | help",
    "skin": "(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'oxide-dark' : 'oxide')",
    "content_css": "(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default')",
}
TINYMCE_SPELLCHECKER = False
TINYMCE_COMPRESSOR = True
