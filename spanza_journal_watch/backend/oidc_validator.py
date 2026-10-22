from oauth2_provider.oauth2_validators import OAuth2Validator

from spanza_journal_watch.utils.functions import split_honorific


def _display_name(user):
    """Return a sensible display name from the custom User model.

    The honorific is dropped: Planka builds avatar initials from the name, so
    "Dr Jane Smith" would show as "DJ" and every doctor would start with D.
    """
    name = getattr(user, "name", None)
    if name and name.strip() and name.strip() != "None":
        return split_honorific(name)[1] or name.strip()
    return user.email


class OIDCValidator(OAuth2Validator):
    """Extend DOT's default validator to include email and name in OIDC claims."""

    oidc_claim_scope = {
        "sub": "openid",
        "email": "email",
        "email_verified": "email",
        "name": "profile",
    }

    def get_additional_claims(self, request=None):
        return {
            "email": lambda r: r.user.email,
            "email_verified": True,
            "name": lambda r: _display_name(r.user),
        }
