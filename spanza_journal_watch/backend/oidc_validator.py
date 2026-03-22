from oauth2_provider.oauth2_validators import OAuth2Validator


def _display_name(user):
    """Return a sensible display name from the custom User model."""
    name = getattr(user, "name", None)
    if name and name.strip() and name.strip() != "None":
        return name.strip()
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
