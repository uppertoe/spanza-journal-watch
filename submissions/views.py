from django.views.generic import DetailView, ListView

from . import models


class PageviewMixin:
    """
    Uses a list of viewed objects in the session
    Calls obj.increment_pageview() and obj.save() if
    an object has not already been viewed
    """

    def get_object(self):
        obj = super().get_object
        # Create an empty list if none exists
        viewed_objects = self.request.session.get("viewed_objects", [])
        if obj not in viewed_objects:
            models.Hit.update_page_count(obj)
        self.request.session["viewed_objects"] = viewed_objects + obj
        return obj


class ReviewDetailView(PageviewMixin, DetailView):
    model = models.Review
    context_object_name = "review"
    template_name = "reviews/review_detail.html"


class ReviewListView(ListView):
    model = models.Review
    context_object_name = "review_list"
    template_name = "reviews/review_list.html"
    queryset = models.Review.objects.all().exclude(active=False).order_by("-created")


class IssueDetailView(PageviewMixin, DetailView):
    model = models.Issue
    context_object_name = "issue"
    template_name = "issues/issue_detail.html"


class IssueListView(ListView):
    model = models.Issue
    context_object_name = "issue_list"
    template_name = "issues/issue_list.html"
    queryset = models.Issue.objects.all().exclude(active=False).order_by("-created")


class TagListView(ListView):
    model = models.Tag
    context_object_name = "tag_list"
    template_name = "tags/tag_list.html"
    queryset = models.Tag.objects.all().exclude(active=False).order_by("text")


class TagDetailView(DetailView):
    model = models.Tag
    context_object_name = "tag"
    template_name = "tags/tag_detail.html"
