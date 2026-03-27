from django import template

from spanza_journal_watch.utils.image_variants import image_variant_name

register = template.Library()


@register.filter
def image_variant_url(image_field, width):
    if not image_field:
        return ""
    return image_field.storage.url(image_variant_name(image_field.name, width))


@register.simple_tag
def image_variant_srcset(image_field, *widths):
    if not image_field:
        return ""

    candidates = []
    for width in widths:
        width = int(width)
        candidates.append(f"{image_field.storage.url(image_variant_name(image_field.name, width))} {width}w")
    return ", ".join(candidates)
