import json

from django import template
from django.db.models import Model
from django.urls import NoReverseMatch, reverse
from django.utils.safestring import mark_safe
from view_breadcrumbs.templatetags.view_breadcrumbs import CONTEXT_KEY

register = template.Library()


def _crumb_url(request, viewname, view_args, view_kwargs):
    if isinstance(viewname, Model) and hasattr(viewname, "get_absolute_url"):
        path = viewname.get_absolute_url()
    elif not viewname:
        return ""
    elif isinstance(viewname, str) and viewname.startswith(("/", "http://", "https://")):
        path = viewname
    else:
        try:
            path = reverse(viewname, args=view_args, kwargs=view_kwargs)
        except NoReverseMatch:
            return ""
    return request.build_absolute_uri(path)


@register.simple_tag(takes_context=True)
def breadcrumb_structured_data(context):
    """Emit schema.org BreadcrumbList JSON-LD mirroring the page's visible breadcrumbs."""
    request = context.get("request")
    if request is None:
        return ""
    crumbs = request.META.get(CONTEXT_KEY, [])
    if len(crumbs) < 2:
        return ""

    items = []
    for position, (label, viewname, view_args, view_kwargs) in enumerate(crumbs, start=1):
        item = {"@type": "ListItem", "position": position, "name": str(label)}
        url = _crumb_url(request, viewname, view_args, view_kwargs)
        if url:
            item["item"] = url
        items.append(item)

    data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
    payload = json.dumps(data).replace("</", "<\\/")
    return mark_safe(f'<script type="application/ld+json">{payload}</script>')  # noqa: S308
