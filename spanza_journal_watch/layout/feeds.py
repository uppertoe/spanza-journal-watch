from django.contrib.syndication.views import Feed

from spanza_journal_watch.submissions.models import Review


class LatestReviewsFeed(Feed):
    title = "SPANZA Journal Watch"
    description = "Latest reviews from the paediatric anaesthesia literature."
    link = "/"

    def items(self):
        return (
            Review.objects.filter(active=True).select_related("article__journal", "author").order_by("-created")[:20]
        )

    def item_title(self, item):
        return item.article.get_title()

    def item_description(self, item):
        return item.get_truncated_body()

    def item_link(self, item):
        return item.get_absolute_url()

    def item_author_name(self, item):
        return str(item.author) if item.author else None

    def item_pubdate(self, item):
        return item.created
