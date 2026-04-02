from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.contrib.auth import get_user_model
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from spanza_journal_watch.backend.models import WatchedJournal

User = get_user_model()


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):
        model = User
        field_classes = {"email": EmailField}


class UserAdminCreationForm(admin_forms.UserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }


class UserSignupForm(SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields will be added automatically.
    Check UserSocialSignupForm for accounts created from social.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"] = forms.CharField(label=_("Full name"), required=False, max_length=255)
        self.fields["subscribe_to_newsletter"] = forms.BooleanField(
            label=_("Subscribe to the Journal Watch newsletter"),
            required=False,
            initial=True,
        )

    def save(self, request):
        user = super().save(request)
        user.name = (self.cleaned_data.get("name") or user.name or "").strip()
        user.save(update_fields=["name"])

        # Optionally subscribe to newsletter
        if self.cleaned_data.get("subscribe_to_newsletter"):
            from spanza_journal_watch.newsletter.models import Subscriber

            subscriber, created = Subscriber.objects.get_or_create(
                email__iexact=user.email,
                defaults={"email": user.email},
            )
            if not created and not subscriber.subscribed:
                subscriber.subscribed = True
                subscriber.save(update_fields=["subscribed", "modified"])
            request.session["subscribed"] = True
            request.session["subscriber_id"] = subscriber.pk

        # Migrate session-based starred articles to this new user
        from spanza_journal_watch.users.utils import migrate_session_stars_to_user

        migrate_session_stars_to_user(request.session, user)

        return user


class UserSocialSignupForm(SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts.
    Default fields will be added automatically.
    See UserSignupForm otherwise.
    """


class UserPreferencesForm(admin_forms.UserChangeForm):
    watched_journals = forms.ModelMultipleChoiceField(
        queryset=WatchedJournal.objects.filter(active=True).order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label=_("Watched journals"),
        help_text=_("Choose the journals that should be preselected in the journals browser."),
    )

    class Meta:
        model = User
        fields = ("name", "watched_journals")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("password", None)
        if self.instance.pk:
            self.fields["watched_journals"].initial = self.instance.watched_journals.filter(active=True)

    def save(self, commit=True):
        user = super().save(commit=commit)
        if user.pk:
            user.watched_journals.set(self.cleaned_data["watched_journals"])
        return user
