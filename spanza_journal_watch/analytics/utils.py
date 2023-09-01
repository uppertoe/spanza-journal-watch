from django.template.loader import render_to_string

from spanza_journal_watch.utils.functions import get_domain_url


def click_tracker(email):
    context = {"email": email, "domain": get_domain_url()}
    template = "analytics/click_tracker.txt"
    return render_to_string(template, context)
