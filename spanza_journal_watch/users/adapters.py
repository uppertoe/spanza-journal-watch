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

            from spanza_journal_watch.backend.models import IssueContributorInvite

            token_hash = IssueContributorInvite.hash_token(token)
            return IssueContributorInvite.objects.filter(
                token_hash=token_hash,
                expires_at__gt=timezone.now(),
                consumed_at__isnull=True,
            ).exists()
        return False

    def get_login_redirect_url(self, request):
        """Send editorial staff to the role-aware destination chooser."""
        if request.user.is_staff:
            return reverse("backend:backend_go")
        return super().get_login_redirect_url(request)
