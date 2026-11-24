from allauth.account.views import LoginView as AllauthLoginView
from allauth.account.views import SignupView as AllauthSignupView
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, RedirectView, UpdateView

from spanza_journal_watch.users.forms import UserPreferencesForm

User = get_user_model()


class UserDetailView(UserPassesTestMixin, DetailView):
    model = User
    slug_field = "id"
    slug_url_kwarg = "id"
    raise_exception = True

    def test_func(self):
        return self.request.user.id == self.get_object().id


user_detail_view = UserDetailView.as_view()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    form_class = UserPreferencesForm
    success_message = _("Information successfully updated")

    def get_success_url(self):
        assert self.request.user.is_authenticated  # for mypy to know that the user is authenticated
        return self.request.user.get_absolute_url()

    def get_object(self):
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self):
        return reverse("users:detail", kwargs={"pk": self.request.user.pk})


user_redirect_view = UserRedirectView.as_view()


@login_required
def delete_account_view(request):
    """Confirmation page for a reader closing their own account; the POST deletes it."""
    from django.contrib import messages
    from django.contrib.auth import logout
    from django.shortcuts import redirect

    from spanza_journal_watch.newsletter.models import Subscriber
    from spanza_journal_watch.users.deletion import can_delete_own_account, delete_account, published_review_count

    user = request.user
    allowed = can_delete_own_account(user)
    if request.method == "POST" and allowed:
        # Deleting first means a failed deletion leaves the reader signed in to try again.
        delete_account(user)
        logout(request)
        messages.success(request, "Your account has been deleted.")
        return redirect("home")

    is_subscribed = Subscriber.by_email(user.email).filter(subscribed=True).exists() or (
        Subscriber.objects.filter(user=user, subscribed=True).exists()
    )
    context = {
        "allowed": allowed,
        "is_subscribed": is_subscribed,
        "review_count": published_review_count(user),
    }
    return render(request, "users/delete_account.html", context)


@login_required
@require_POST
def update_profile_name(request):
    """Update the user's display name from the profile drawer."""
    name = (request.POST.get("name") or "").strip()[:255]
    request.user.name = name
    request.user.save(update_fields=["name"])
    return render(request, "fragments/user_profile_name.html")


def _invite_email_from_request(request):
    """Return invited email from session or query string (survives logout redirect)."""
    return request.session.get("pending_invite_email", "") or request.GET.get("invite_email", "")


def _next_hint(next_url):
    """A sentence explaining why a page sent someone here, for the sign-in form."""
    if next_url.startswith("/cpd/"):
        return (
            "Your CPD report is built from the articles you open while signed in, "
            "so it needs an account. Sign in, or enter your email to create one."
        )
    return ""


class InviteAwareLoginView(AllauthLoginView):
    """Allauth LoginView that pre-fills and locks the email when coming from an invite."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invite_email"] = _invite_email_from_request(self.request)
        ctx["next_hint"] = _next_hint(self.request.GET.get("next") or "")
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        invite_email = _invite_email_from_request(self.request)
        if invite_email and self.request.method == "GET":
            kwargs.setdefault("initial", {})["login"] = invite_email
        return kwargs


invite_aware_login_view = InviteAwareLoginView.as_view()


class InviteAwareSignupView(AllauthSignupView):
    """Allauth SignupView that pre-fills and locks the email when coming from an invite.

    Both sign-up routes end here: /users/~start/ hands a new address to this view,
    and /accounts/signup/ is the view itself. The honeypot, the checks in
    signup_guard and the hourly cap are therefore applied here, so posting
    straight to /accounts/signup/ passes the same guards. Invited contributors
    face only the honeypot.
    """

    def dispatch(self, request, *args, **kwargs):
        # Checked ahead of allauth's dispatch, which would otherwise send a browser
        # with a sign-up still pending to its confirmation page first.
        if request.method != "POST":
            return super().dispatch(request, *args, **kwargs)

        from allauth.account.adapter import get_adapter
        from django.contrib import messages
        from django.shortcuts import redirect

        from spanza_journal_watch.users import signup_guard
        from spanza_journal_watch.users.adapters import pending_invite_email

        if request.POST.get(HONEYPOT_FIELD):
            # Only a bot fills the hidden field. Return to the form without saying why.
            signup_guard.log_refusal("honeypot")
            return redirect("account_signup")
        if pending_invite_email(request) is not None:
            # An editor invited this address, so the remaining checks do not apply.
            return super().dispatch(request, *args, **kwargs)

        reason = signup_guard.scripted_signup_reason(request)
        if reason == signup_guard.SCRIPTED_ACCEPT_LANGUAGE:
            # No browser writes this header, so the form returns without saying why.
            signup_guard.log_refusal(reason)
            return redirect("account_signup")
        if reason == signup_guard.STALE_CHROMIUM_WITHOUT_PRIORITY:
            signup_guard.log_refusal(reason)
            messages.error(
                request,
                "We have not been able to create your account from this browser. "
                "If you are using an older version of Chrome, please update it and try again. "
                "If you still cannot sign up, you can contact us for assistance.",
            )
            return redirect("account_signup")
        if not signup_guard.ipv6_prefix_signup_allowed(get_adapter(request).get_client_ip(request)):
            signup_guard.log_refusal("ipv6_prefix_daily_limit")
            messages.error(
                request,
                "We are not able to create another account from this connection today. "
                "Please try again tomorrow, or you can contact us for assistance.",
            )
            return redirect("account_signup")
        if not _new_signup_allowed():
            signup_guard.log_refusal("hourly_limit")
            messages.error(
                request, "We are not able to create new accounts at the moment. Please try again in an hour."
            )
            return redirect("account_login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invite_email"] = _invite_email_from_request(self.request)
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        invite_email = _invite_email_from_request(self.request)
        if invite_email and self.request.method == "GET":
            kwargs.setdefault("initial", {})["email"] = invite_email
        return kwargs


invite_aware_signup_view = InviteAwareSignupView.as_view()


# Field the sign-in form hides from people; form-filling bots complete it.
HONEYPOT_FIELD = "jw_hp_extra"


def _new_signup_allowed():
    """Cap new accounts site-wide per clock hour.

    allauth's own signup limit is per IP; this site-wide cap stops a bot that
    rotates addresses from creating accounts (and sending verification emails)
    at volume.
    """
    from django.core.cache import cache
    from django.utils import timezone

    limit = getattr(settings, "SIGNUP_HOURLY_LIMIT", 30)
    key = f"users:new-signups:{timezone.now():%Y%m%d%H}"
    cache.add(key, 0, timeout=2 * 60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:
        # The dummy cache used in local development keeps nothing.
        return True
    return count <= limit


@require_POST
def email_start(request):
    """One email field that covers both sign-in and sign-up.

    Members are usually known to us by email alone, so the entry point asks for
    nothing else. An existing account is handed to allauth's sign-in-code flow;
    a new address is signed up on the spot (email only) and sent a verification
    code. Either way the next page is the code entry form.
    """
    from allauth.account.adapter import get_adapter
    from allauth.account.views import RequestLoginCodeView
    from django.contrib import messages
    from django.core.validators import validate_email
    from django.shortcuts import redirect

    if request.POST.get(HONEYPOT_FIELD):
        # Only a bot fills the hidden field. Return to the form without saying why.
        return redirect("account_login")

    email = (request.POST.get("email") or "").strip()
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Please enter a valid email address.")
        return redirect("account_login")

    if User.objects.filter(email__iexact=email).exists():
        return RequestLoginCodeView.as_view()(request)

    if not get_adapter(request).is_open_for_signup(request):
        messages.error(request, "There is no account for that address, and registration is closed.")
        return redirect("account_login")
    return invite_aware_signup_view(request)
