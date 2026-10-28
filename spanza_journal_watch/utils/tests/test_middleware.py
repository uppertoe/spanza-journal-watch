from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from spanza_journal_watch.utils.middleware import LoggingMiddleware


def _view(request):
    upload = request.FILES["feature_image"]
    return HttpResponse(str(upload.size))


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1000)
def test_upload_larger_than_data_limit_reaches_the_view():
    upload = SimpleUploadedFile("photo.jpg", b"x" * 5000, content_type="image/jpeg")
    request = RequestFactory().post("/upload", {"title": "Review", "feature_image": upload})

    response = LoggingMiddleware(_view)(request)

    assert response.status_code == 200
    assert response.content == b"5000"


def test_non_multipart_body_still_cached_for_logging():
    request = RequestFactory().post("/form", {"title": "Review"}, content_type="application/x-www-form-urlencoded")
    seen = {}

    def view(req):
        seen["body"] = req.body
        return HttpResponse("ok")

    assert LoggingMiddleware(view)(request).status_code == 200
    assert seen["body"]


def test_failed_request_body_is_not_logged(caplog):
    request = RequestFactory().post(
        "/accounts/login/",
        {"login": "member@example.com", "code": "123456"},
        content_type="application/x-www-form-urlencoded",
    )

    with caplog.at_level("DEBUG", logger="django.request"):
        LoggingMiddleware(lambda req: HttpResponse(status=429))(request)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "POST /accounts/login/" in logged
    assert "member@example.com" not in logged
    assert "123456" not in logged
