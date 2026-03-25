from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from spanza_journal_watch.users.forms import UserAdminChangeForm, UserAdminCreationForm

User = get_user_model()

admin.site.site_header = "Journal Watch Administration"
admin.site.site_title = "Journal Watch Admin"
admin.site.index_title = "Site administration"


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name",)}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    list_display = ["email", "name", "is_staff", "is_superuser", "last_login", "date_joined"]
    list_filter = ["is_staff", "is_superuser", "is_active", "groups"]
    search_fields = ["email", "name"]
    ordering = ["-date_joined"]
    readonly_fields = ["last_login", "date_joined"]
    filter_horizontal = ["groups", "user_permissions"]
