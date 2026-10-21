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
