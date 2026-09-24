"""The warm-up must never render under an internal hostname.

AnonymousCacheMixin keys cached anonymous responses on the path, not the host,
so a page warmed under a name like `jw-django` would be stored under the key a
real visitor reads and could serve them absolute URLs pointing at that name.
The host choice is therefore load-bearing, not cosmetic.
"""

import pytest
from django.test import override_settings

from config.gunicorn_conf import _public_host


@override_settings(ALLOWED_HOSTS=[".journalwatch.org.au", "jw-django", "django"])
def test_prefers_the_public_domain_over_internal_names():
    assert _public_host() == "journalwatch.org.au"


@override_settings(ALLOWED_HOSTS=["jw-django", "django"])
def test_returns_nothing_when_only_internal_names_are_allowed():
    # Better to skip warming than to poison the shared cache.
    assert _public_host() is None


@override_settings(ALLOWED_HOSTS=[".journalwatch.org.au"])
def test_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("JW_WARM_HOST", "staging.journalwatch.org.au")
    assert _public_host() == "staging.journalwatch.org.au"


@override_settings(ALLOWED_HOSTS=["example.com"])
@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_disabled_by_env(monkeypatch, value):
    """JW_WARM_ON_START=0 must stop the hook doing any work at all."""
    from config import gunicorn_conf

    monkeypatch.setenv("JW_WARM_ON_START", value)
    called = []

    class Worker:
        class log:
            @staticmethod
            def info(*a, **k):
                called.append(a)

            @staticmethod
            def warning(*a, **k):
                called.append(a)

    gunicorn_conf.post_worker_init(Worker())
    assert called == []
