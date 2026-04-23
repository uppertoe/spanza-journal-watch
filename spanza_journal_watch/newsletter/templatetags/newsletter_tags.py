from django import template

from ..forms import SubscriberForm

register = template.Library()


@register.inclusion_tag("newsletter/_drawer_subscribe_form.html")
def drawer_subscribe_form():
    return {"form": SubscriberForm()}


@register.inclusion_tag("newsletter/_drawer_subscribed_panel.html", takes_context=True)
def drawer_subscribed_panel(context):
    request = context.get("request")
    subscriber_email = None
    can_unsubscribe_from_drawer = False
    if request is not None and hasattr(request, "session"):
        subscriber_id = request.session.get("subscriber_id")
        subscriber_email = request.session.get("subscriber_email")
        if subscriber_id:
            can_unsubscribe_from_drawer = True
    return {
        "subscriber_email": subscriber_email,
        "can_unsubscribe_from_drawer": can_unsubscribe_from_drawer,
    }
