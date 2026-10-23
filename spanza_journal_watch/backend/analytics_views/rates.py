"""Newsletter open and click rates: the one definition every page and email reports.

Real opens are pixel loads not flagged as automated. Machine opens are Apple Mail
Privacy Protection prefetches (``automated_reason="apple_mpp"``), which say the
email reached an Apple inbox but not that anyone read it. A real click proves the
email was opened even when the pixel was blocked, so "opened" is the union of real
openers and real clickers, and click-to-open can never exceed 100%. Testers are
excluded from every figure, including the emails-sent denominator.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Count, Q

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import NewsletterDelivery

MACHINE_OPEN_REASON = "apple_mpp"


def _percentage(numerator, denominator):
    if not denominator:
        return "0%"
    return f"{round(numerator / denominator * 100)}%"


@dataclass
class NewsletterRates:
    emails_sent: int = 0
    real_openers: int = 0
    machine_openers: int = 0
    real_clickers: int = 0
    opened: int = 0
    open_events: int = 0
    real_open_events: int = 0
    click_events: int = 0
    real_click_events: int = 0
    link_clicks: list = field(default_factory=list)

    @property
    def open_rate_value(self):
        return self.opened / self.emails_sent if self.emails_sent else 0

    @property
    def click_rate_value(self):
        return self.real_clickers / self.emails_sent if self.emails_sent else 0

    @property
    def open_rate(self):
        return _percentage(self.opened, self.emails_sent)

    @property
    def click_rate(self):
        return _percentage(self.real_clickers, self.emails_sent)

    @property
    def click_to_open(self):
        return _percentage(self.real_clickers, self.opened)

    @property
    def automated_open_share(self):
        return _percentage(self.open_events - self.real_open_events, self.open_events)

    @property
    def automated_click_share(self):
        return _percentage(self.click_events - self.real_click_events, self.click_events)


def newsletter_rates(newsletters, link_limit=8):
    """Return ``{newsletter.pk: NewsletterRates}`` in a fixed number of queries."""
    newsletters = list(newsletters)
    ids = [nl.pk for nl in newsletters]
    rates = {nl.pk: NewsletterRates() for nl in newsletters}
    if not ids:
        return rates

    # ``emails_sent`` counts every recipient, testers included; deliveries say which were testers.
    tester_deliveries = dict(
        NewsletterDelivery.objects.filter(newsletter_id__in=ids, subscriber__tester=True)
        .values("newsletter_id")
        .annotate(c=Count("id"))
        .values_list("newsletter_id", "c")
    )
    for nl in newsletters:
        rates[nl.pk].emails_sent = max((nl.emails_sent or 0) - tester_deliveries.get(nl.pk, 0), 0)

    opens = NewsletterOpen.objects.filter(newsletter_id__in=ids).exclude(subscriber__tester=True)
    clicks = NewsletterClick.objects.filter(newsletter_id__in=ids).exclude(subscriber__tester=True)

    for row in opens.values("newsletter_id").annotate(
        total=Count("id"),
        real=Count("id", filter=Q(automated=False)),
        machine=Count("subscriber", distinct=True, filter=Q(automated_reason=MACHINE_OPEN_REASON)),
    ):
        nl_rates = rates[row["newsletter_id"]]
        nl_rates.open_events = row["total"]
        nl_rates.real_open_events = row["real"]
        nl_rates.machine_openers = row["machine"]
    for row in clicks.values("newsletter_id").annotate(total=Count("id"), real=Count("id", filter=Q(automated=False))):
        nl_rates = rates[row["newsletter_id"]]
        nl_rates.click_events = row["total"]
        nl_rates.real_click_events = row["real"]

    openers = defaultdict(set)
    for nl_id, sub_id in opens.filter(automated=False).values_list("newsletter_id", "subscriber_id").distinct():
        openers[nl_id].add(sub_id)
    clickers = defaultdict(set)
    for nl_id, sub_id in clicks.filter(automated=False).values_list("newsletter_id", "subscriber_id").distinct():
        clickers[nl_id].add(sub_id)
    for nl_id, nl_rates in rates.items():
        nl_rates.real_openers = len(openers[nl_id])
        nl_rates.real_clickers = len(clickers[nl_id])
        nl_rates.opened = len(openers[nl_id] | clickers[nl_id])

    if link_limit:
        link_rows = (
            clicks.filter(automated=False)
            .exclude(destination_url="")
            .values("newsletter_id", "destination_url")
            .annotate(clicks=Count("id"), unique_subscribers=Count("subscriber", distinct=True))
            .order_by("newsletter_id", "-clicks")
        )
        for row in link_rows:
            links = rates[row["newsletter_id"]].link_clicks
            if len(links) < link_limit:
                links.append(row)

    return rates


def newsletter_rate(newsletter, link_limit=0):
    """Rates for a single newsletter."""
    return newsletter_rates([newsletter], link_limit=link_limit)[newsletter.pk]
