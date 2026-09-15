from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def track(context, destination):
    """Route ``destination`` through the email click redirector.

    Expects an ``EmailLinkBuilder`` as ``tracker`` in the context. Without one
    (previews, tests) the destination is returned untouched.
    """
    tracker = context.get("tracker")
    if not tracker:
        return destination
    return tracker.url(destination)
