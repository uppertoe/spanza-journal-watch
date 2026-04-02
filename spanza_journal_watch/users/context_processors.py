from django.conf import settings


def allauth_settings(request):
    """Expose some settings from django-allauth in templates."""
    ctx = {
        "ACCOUNT_ALLOW_REGISTRATION": settings.ACCOUNT_ALLOW_REGISTRATION,
    }

    # Newsletter subscription state for the profile drawer
    if request.user.is_authenticated:
        from spanza_journal_watch.newsletter.models import Subscriber

        ctx["user_is_subscribed"] = Subscriber.objects.filter(
            email__iexact=request.user.email,
            subscribed=True,
        ).exists()

    return ctx
