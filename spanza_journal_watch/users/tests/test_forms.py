"""
Module for all Form Tests.
"""

from django.utils.translation import gettext_lazy as _

from spanza_journal_watch.backend.models import WatchedJournal
from spanza_journal_watch.users.forms import UserAdminCreationForm, UserPreferencesForm, UserSignupForm
from spanza_journal_watch.users.models import User


class TestUserAdminCreationForm:
    """
    Test class for all tests related to the UserAdminCreationForm
    """

    def test_username_validation_error_msg(self, user: User):
        """
        Tests UserAdminCreation Form's unique validator functions correctly by testing:
            1) A new user with an existing username cannot be added.
            2) Only 1 error is raised by the UserCreation Form
            3) The desired error message is raised
        """

        # The user already exists,
        # hence cannot be created.
        form = UserAdminCreationForm(
            {
                "email": user.email,
                "password1": user.password,
                "password2": user.password,
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "email" in form.errors
        assert form.errors["email"][0] == _("This email has already been taken.")


class TestUserSignupForm:
    def test_signup_form_includes_name_field(self):
        form = UserSignupForm()
        assert "name" in form.fields


class TestUserPreferencesForm:
    def test_saves_watched_journals(self, user: User):
        watched_one = WatchedJournal.objects.create(name="Journal One", active=True)
        watched_two = WatchedJournal.objects.create(name="Journal Two", active=True)
        form = UserPreferencesForm(
            data={"name": "Updated Name", "watched_journals": [watched_one.pk, watched_two.pk]},
            instance=user,
        )

        assert form.is_valid(), form.errors
        saved_user = form.save()

        assert saved_user.name == "Updated Name"
        assert set(saved_user.watched_journals.values_list("pk", flat=True)) == {watched_one.pk, watched_two.pk}
