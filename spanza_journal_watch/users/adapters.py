from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def pending_invite_email(request):
    """Return the address invited by the session's pending invite, if it is still usable."""
    token = request.session.get("_pending_invite_token")
    if not token:
        return None
    from django.utils import timezone

    from spanza_journal_watch.backend.models import ChiefEditorInvite, IssueContributorInvite

    token_hash = IssueContributorInvite.hash_token(token)
    now = timezone.now()
    contributor_invite = (
        IssueContributorInvite.objects.filter(token_hash=token_hash, expires_at__gt=now, consumed_at__isnull=True)
        .select_related("contributor")
        .first()
    )
    if contributor_invite:
        return contributor_invite.contributor.email or ""
    chief_editor_invite = ChiefEditorInvite.objects.filter(
        token_hash=token_hash, expires_at__gt=now, consumed_at__isnull=True
    ).first()
    if chief_editor_invite:
        return chief_editor_invite.email or ""
    return None


def _matches_invite(request, user):
    """True when the user's address is the one the pending invite was sent to.

    An invite link proves control of the invited address only, so an account
    created or signed in under any other address is verified the usual way.
    """
    invited = (pending_invite_email(request) or "").strip().lower()
    return bool(invited) and invited == (user.email or "").strip().lower()


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        if getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", False):
            return True
        # Allow signup only when arriving from a valid, unconsumed invite link.
        # The invite view sets this session key when an unauthenticated user visits.
        return pending_invite_email(request) is not None

    def save_user(self, request, user, form, commit=True):
        """Flag user for email auto-verification when arriving from an invite link.

        The actual EmailAddress record is created later by allauth's
        ``setup_user_email``.  We must not create it here or the assertion in
        ``setup_user_email`` will fail.  Instead we stash a flag on the user
        instance and verify the address in ``confirm_email_on_invite`` (called
        from the signup form's ``save`` after ``setup_user_email`` has run).
        """
        user = super().save_user(request, user, form, commit=commit)
        if commit and _matches_invite(request, user):
            user._invite_verify_email = True
        return user

    @staticmethod
    def confirm_email_on_invite(user):
        """Verify the primary EmailAddress created by allauth after signup."""
        if not getattr(user, "_invite_verify_email", False):
            return
        from allauth.account.models import EmailAddress

        EmailAddress.objects.filter(user=user, email__iexact=user.email, verified=False).update(
            verified=True, primary=True
        )

    def login(self, request, user):
        """Migrate session stars/full-text clicks and link any orphaned Subscriber on login."""
        from spanza_journal_watch.newsletter.models import Subscriber
        from spanza_journal_watch.users.utils import (
            migrate_session_fulltext_to_user,
            migrate_session_recommendations_to_user,
            migrate_session_stars_to_user,
        )

        migrate_session_stars_to_user(request.session, user)
        migrate_session_recommendations_to_user(request.session, user)
        migrate_session_fulltext_to_user(request.session, user)
        Subscriber.objects.filter(email__iexact=user.email, user__isnull=True).update(user=user)

        # Auto-verify email when logging in via an invite link.
        # The invite link itself is proof the user controls the invited address,
        # so skip the email verification step that would otherwise block login.
        if _matches_invite(request, user):
            from allauth.account.models import EmailAddress

            email_obj = EmailAddress.objects.filter(user=user, email__iexact=user.email).first()
            if email_obj and not email_obj.verified:
                email_obj.verified = True
                email_obj.primary = True
                email_obj.save(update_fields=["verified", "primary"])
            elif not email_obj:
                EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        return super().login(request, user)

    def get_client_ip(self, request):
        """Use the address Caddy forwards, so sign-in limits apply per reader.

        Without CLIENT_IP_HEADER, or when a request arrives without it, the
        connecting address is used as allauth would.
        """
        header = getattr(settings, "CLIENT_IP_HEADER", "")
        forwarded = request.headers.get(header, "").strip() if header else ""
        if forwarded:
            from allauth.core.internal.httpkit import clean_client_ip

            ip = clean_client_ip(forwarded)
            if ip:
                return ip
        return super().get_client_ip(request)

    def get_login_redirect_url(self, request):
        """Respect safe ?next= values; otherwise go to home page."""
        next_url = request.POST.get("next") or request.GET.get("next") or ""
        allowed_hosts = {request.get_host()} if request else set()
        allowed_hosts.update(host for host in getattr(settings, "ALLOWED_HOSTS", []) if host and host != "*")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts=allowed_hosts,
            require_https=request.is_secure(),
        ):
            return next_url
        return "/"
