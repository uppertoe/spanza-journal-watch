from django.conf import settings
from django.template import engines
from django.test import override_settings

from spanza_journal_watch.utils.template_warmup import warm_template_cache, warm_url_resolver


@override_settings(TEMPLATE_WARMUP=True)
def test_compiles_every_project_template():
    templates_dir = settings.APPS_DIR / "templates"
    expected = len(list(templates_dir.rglob("*.html")))
    compiled = warm_template_cache()
    assert compiled == expected > 100
    loader = engines["django"].engine.template_loaders[0]
    assert loader.__class__.__module__ == "django.template.loaders.cached"
    assert len(loader.get_template_cache) >= compiled


@override_settings(TEMPLATE_WARMUP=False)
def test_disabled_by_setting():
    assert warm_template_cache() == 0


@override_settings(TEMPLATE_WARMUP=True)
def test_unloadable_templates_are_skipped(tmp_path):
    (tmp_path / "broken.html").write_text("{% if %}")
    assert warm_template_cache(template_dir=tmp_path) == 0


@override_settings(TEMPLATE_WARMUP=True)
def test_url_resolver_warmup_populates_reverse_tables():
    assert warm_url_resolver() is True
    from django.urls import get_resolver

    assert get_resolver()._populated is True


@override_settings(TEMPLATE_WARMUP=False)
def test_url_resolver_warmup_disabled_by_setting():
    assert warm_url_resolver() is False
