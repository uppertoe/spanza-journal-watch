from allauth.account.views import LoginView as AllauthLoginView
from allauth.account.views import SignupView as AllauthSignupView
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
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


def _invite_email_from_session(request):
    """Return invited email stored in session when arriving from an invite link."""
    return request.session.get("pending_invite_email", "")


class InviteAwareLoginView(AllauthLoginView):
    """Allauth LoginView that pre-fills and locks the email when coming from an invite."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invite_email"] = _invite_email_from_session(self.request)
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        invite_email = _invite_email_from_session(self.request)
        if invite_email and self.request.method == "GET":
            kwargs.setdefault("initial", {})["login"] = invite_email
        return kwargs


invite_aware_login_view = InviteAwareLoginView.as_view()


class InviteAwareSignupView(AllauthSignupView):
    """Allauth SignupView that pre-fills and locks the email when coming from an invite."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invite_email"] = _invite_email_from_session(self.request)
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        invite_email = _invite_email_from_session(self.request)
        if invite_email and self.request.method == "GET":
            kwargs.setdefault("initial", {})["email"] = invite_email
        return kwargs


invite_aware_signup_view = InviteAwareSignupView.as_view()
