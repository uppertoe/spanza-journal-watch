from django import template
from django.utils.safestring import mark_safe
from webpack_loader.utils import get_loader

register = template.Library()


@register.simple_tag
def webpack_font_preloads():
    assets = get_loader("DEFAULT").get_assets().get("assets", {})
    tags = []
    for name, info in assets.items():
        if name.endswith(".woff2"):
            url = info.get("publicPath", "")
            tags.append(f'<link rel="preload" href="{url}" as="font"' f' type="font/woff2" crossorigin>')
    return mark_safe("\n    ".join(tags))
