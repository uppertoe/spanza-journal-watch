from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        if getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", False):
            return True
        # Allow signup only when arriving from a valid, unconsumed invite link.
        # The invite view sets this session key when an unauthenticated user visits.
        token = request.session.get("_pending_invite_token")
        if token:
            from django.utils import timezone

            from spanza_journal_watch.backend.models import ChiefEditorInvite, IssueContributorInvite

            token_hash = IssueContributorInvite.hash_token(token)
            now = timezone.now()
            if IssueContributorInvite.objects.filter(
                token_hash=token_hash,
                expires_at__gt=now,
                consumed_at__isnull=True,
            ).exists():
                return True
            if ChiefEditorInvite.objects.filter(
                token_hash=token_hash,
                expires_at__gt=now,
                consumed_at__isnull=True,
            ).exists():
                return True
        return False

    def login(self, request, user):
        """Migrate session stars/full-text clicks and link any orphaned Subscriber on login."""
        from spanza_journal_watch.newsletter.models import Subscriber
        from spanza_journal_watch.users.utils import migrate_session_fulltext_to_user, migrate_session_stars_to_user

        migrate_session_stars_to_user(request.session, user)
        migrate_session_fulltext_to_user(request.session, user)
        Subscriber.objects.filter(email__iexact=user.email, user__isnull=True).update(user=user)

        # Auto-verify email when logging in via an invite link.
        # The invite link itself is proof the user controls this email address,
        # so skip the email verification step that would otherwise block login.
        if request.session.get("_pending_invite_token"):
            from allauth.account.models import EmailAddress

            email_obj = EmailAddress.objects.filter(user=user, email__iexact=user.email).first()
            if email_obj and not email_obj.verified:
                email_obj.verified = True
                email_obj.primary = True
                email_obj.save(update_fields=["verified", "primary"])
            elif not email_obj:
                EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        return super().login(request, user)

    def get_login_redirect_url(self, request):
        """Respect ?next= when present; otherwise always go to journals.

        Editorial users reach the backend via the 'Editor' button in the nav,
        not via automatic login redirect.
        """
        next_url = request.POST.get("next") or request.GET.get("next") or ""
        if next_url:
            return next_url
        return reverse("submissions:journal_list")
