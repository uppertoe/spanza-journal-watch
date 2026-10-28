from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory

from spanza_journal_watch.utils.middleware import StaffSessionLimitMiddleware


def test_staff_session_limit_leaves_anonymous_sessions_unread():
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.user = AnonymousUser()

    StaffSessionLimitMiddleware(lambda req: HttpResponse("ok"))(request)

    # An accessed session would add "Vary: Cookie" to pages served to anonymous readers
    assert not request.session.accessed
