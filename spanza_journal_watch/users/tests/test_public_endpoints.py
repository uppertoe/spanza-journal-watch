import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch, reverse
from PIL import Image

from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG")
    return SimpleUploadedFile("pic.png", buffer.getvalue(), content_type="image/png")


class TestMarkdownxUpload:
    def test_visitors_cannot_upload(self, client):
        response = client.post("/markdownx/upload/", {"image": _png()}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 302
        assert "/admin" in response["Location"] or "login" in response["Location"]

    def test_readers_cannot_upload(self, client):
        client.force_login(UserFactory(is_staff=False))
        response = client.post("/markdownx/upload/", {"image": _png()}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 302

    def test_staff_cannot_upload_markup_disguised_as_an_image(self, client):
        client.force_login(UserFactory(is_staff=True))
        page = SimpleUploadedFile("page.html", b"<script>alert(1)</script>", content_type="image/gif")
        response = client.post("/markdownx/upload/", {"image": page}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 400

    def test_markdownify_is_staff_only(self, client):
        client.force_login(UserFactory(is_staff=False))
        assert client.post("/markdownx/markdownify/", {"content": "**x**"}).status_code == 302


class TestOAuthRoutes:
    @pytest.mark.parametrize(
        "path", ["/o/applications/", "/o/applications/register/", "/o/authorized_tokens/", "/o/device/"]
    )
    def test_management_and_device_routes_are_absent(self, client, path):
        client.force_login(UserFactory(is_staff=False))
        assert client.get(path).status_code == 404

    def test_sign_in_routes_remain(self, client):
        assert client.get("/o/.well-known/openid-configuration").status_code == 200
        for name in ("token", "revoke-token", "user-info", "jwks-info"):
            assert reverse(f"oauth2_provider:{name}")

    def test_registration_is_not_routable(self):
        with pytest.raises(NoReverseMatch):
            reverse("oauth2_provider:register")
