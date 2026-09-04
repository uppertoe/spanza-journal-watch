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
        ctx["user_initials"] = user_initials(request.user)
        ctx["user_short_name"] = user_short_name(request.user)
        ctx["user_masthead_label"] = user_masthead_label(request.user)

    return ctx


def user_masthead_label(user):
    """What the masthead button says: a first name if we have one, otherwise a plain signed-in marker."""
    name = (getattr(user, "name", "") or "").strip()
    return name.split()[0] if name else "Signed in"


def user_short_name(user):
    """First name if we have one, otherwise the part of the email before the @."""
    name = (getattr(user, "name", "") or "").strip()
    if name:
        return name.split()[0]
    email = (getattr(user, "email", "") or "").strip()
    return email.split("@")[0] if email else "Account"


def user_initials(user):
    """One or two letters for the masthead avatar."""
    name = (getattr(user, "name", "") or "").strip()
    parts = [p for p in name.replace("-", " ").split() if p and p[0].isalpha()]
    # Drop honorifics so "Dr Priya Nair" gives PN, not DN.
    parts = [
        p for p in parts if p.rstrip(".").lower() not in {"dr", "prof", "mr", "mrs", "ms", "miss", "a/prof"}
    ] or parts
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    if parts:
        return parts[0][0].upper()
    email = (getattr(user, "email", "") or "").strip()
    return email[0].upper() if email else "?"
