from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import MultipleObjectsReturned
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.newsletter.tasks import send_newsletter, send_newsletter_test_email
from spanza_journal_watch.submissions.models import Issue
from spanza_journal_watch.utils.cache import bump_content_cache_version

from .forms import (
    HeaderForm,
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    NewsletterCreateForm,
    NewsletterTestSendForm,
    SubscriberCSVForm,
    peek_csv,
)
from .models import SubscriberCSV
from .tasks import process_subscriber_csv


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def upload_subscriber_csv(request):
    context = {}

    if request.method == "POST":
        form = SubscriberCSVForm(request.POST, request.FILES)
        context["form"] = form

        if form.is_valid():
            instance = form.save(commit=False)
            header = form.cleaned_data["has_header"]
            instance.header = header  # Save the csv sniffer best guess
            instance.save()

            context["instance"] = instance
            context["preview"] = form.cleaned_data["preview"]
            context["header_form"] = HeaderForm(initial={"header": header})  # include a checkbox for header select

            # HTMX not yet implemented here
            if request.headers.get("HX-Request") == "true":
                template = "backend/preview_csv_htmx.html"
            else:
                template = "backend/preview_csv.html"

            return render(request, template, context)

    else:
        form = SubscriberCSVForm()
        context["form"] = form

    return render(request, "backend/upload_subscribers.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def edit_csv_header(request, save_token):
    # Requires HTMX
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request - HTMX only")

    # Perform a lookup using the token
    try:
        subscriber_csv = SubscriberCSV.objects.get(save_token=save_token)
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned):
        messages.error(request, "There was a problem updating this CSV. Please refresh the page and try again")
        return render(request, "fragments/messages.html")

    if request.method == "POST":
        form = HeaderForm(request.POST)

        if form.is_valid():
            header = form.cleaned_data["header"]
            print(f"here's the header: {header}")
            subscriber_csv.header = header
            subscriber_csv.save()

    else:
        form = HeaderForm(initial={"header": subscriber_csv.header})

    # Re-peek into the CSV
    file = subscriber_csv.file.open()
    peek = peek_csv(file, user_header=subscriber_csv.header)
    file.close()

    context = {"header_form": form, "instance": subscriber_csv}
    context.update(peek)

    return render(request, "backend/preview_csv_htmx.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def process_csv(request, save_token):
    """
    Accessing this endpoint sets the subscriber_csv.confirmed to True
    Saving the object then sends the task to Celery for processing

    Requires a subscriber_csv.save_token
    """
    # Requires HTMX
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request - HTMX only")

    # Perform a lookup using the token
    try:
        subscriber_csv = SubscriberCSV.objects.get(save_token=save_token)
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned):
        messages.error(request, "There was a problem updating this CSV. Please refresh the page and try again")
        return render(request, "fragments/messages.html")

    subscriber_csv.confirmed = True
    subscriber_csv.save()

    # Send the task to Celery
    if subscriber_csv.is_ready_to_process:
        process_subscriber_csv.apply_async((subscriber_csv.pk,), countdown=1)

    # Messages included in the template fragment
    messages.success(request, "CSV successfully sent for processing")

    return render(request, "backend/process_csv_success.html")


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def dashboard(request):
    return render(request, "backend/dashboard.html")


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def newsletter_release_list(request):
    newsletters = Newsletter.objects.order_by("-send_date", "-pk")
    context = {"newsletters": newsletters}
    return render(request, "backend/newsletter_release_list.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def create_newsletter(request):
    if request.method == "POST":
        form = NewsletterCreateForm(request.POST, request.FILES)
        if form.is_valid():
            newsletter = form.save()
            messages.success(request, f"Newsletter {newsletter} created.")
            return redirect(reverse("backend:final_newsletter", kwargs={"send_token": newsletter.send_token}))
    else:
        form = NewsletterCreateForm()

    context = {"form": form}
    return render(request, "backend/create_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def final_newsletter(request, send_token):
    # Provides last check before sending
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    context = {
        "send_token": send_token,
        "newsletter": newsletter,
        "test_form": NewsletterTestSendForm(),
    }
    return render(request, "backend/final_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_test_newsletter(request, send_token):
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    if not newsletter:
        messages.error(request, "This token is no longer valid. Please refresh and try again.")
        return redirect("backend:dashboard")

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    form = NewsletterTestSendForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        send_newsletter_test_email.apply_async((newsletter.pk, email), countdown=1)
        messages.success(request, f"Test newsletter queued for {email}")
    else:
        messages.error(request, "Please enter a valid test email address.")

    return redirect(reverse("backend:final_newsletter", kwargs={"send_token": send_token}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def enable_newsletter_resend(request, send_token):
    newsletter = Newsletter.objects.filter(send_token=send_token).first()
    if not newsletter:
        messages.error(request, "This token is no longer valid. Please refresh and try again.")
        return redirect("backend:dashboard")

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    if not newsletter.is_sent:
        messages.error(request, "This newsletter has not been sent yet.")
    else:
        newsletter.resend_enabled = True
        newsletter.save(update_fields=["resend_enabled", "send_token"])
        messages.warning(request, "Resend enabled for this newsletter. It can now be sent once more.")

    return redirect(reverse("backend:final_newsletter", kwargs={"send_token": newsletter.send_token}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_final_newsletter(request, send_token):
    try:
        newsletter = Newsletter.objects.get(send_token=send_token)
        if newsletter.is_ready_to_send():
            # Celery task also checks is_ready_to_send
            send_newsletter.apply_async((newsletter.pk,), {"test_email": False}, countdown=1)
            messages.success(request, f"Newsletter {newsletter} queued for sending")
        else:
            messages.error(request, f"Newsletter {newsletter} not sent: not ready")

    except Newsletter.DoesNotExist:
        messages.error(request, "This token is no longer valid. Please re-send a test newsletter")
        newsletter = {}

    return render(request, "backend/send_final_newsletter.html", {"newsletter": newsletter})


@login_required
@permission_required("backend.view_newsletter_stats", raise_exception=True)  # Prevents login loop
def newsletter_stats_list(request):
    newsletters = Newsletter.objects.filter(is_sent=True)
    context = {"newsletters": newsletters}
    template = "backend/newsletter_stats_list.html"
    return render(request, template, context)


@login_required
@permission_required("backend.view_newsletter_stats", raise_exception=True)  # Prevents login loop
def newsletter_stats_detail(request, pk):
    newsletter = get_object_or_404(Newsletter, pk=pk)

    opens_qs = NewsletterOpen.objects.filter(newsletter=newsletter)
    clicks_qs = NewsletterClick.objects.filter(newsletter=newsletter)

    subscriber_opens = opens_qs.values("subscriber")
    total_opens = subscriber_opens.count()
    opens = subscriber_opens.distinct().count()

    subscriber_clicks = clicks_qs.values("subscriber")
    total_clicks = subscriber_clicks.count()
    clicks = subscriber_clicks.distinct().count()

    human_subscriber_opens = opens_qs.filter(automated=False).values("subscriber")
    total_human_opens = human_subscriber_opens.count()
    human_opens = human_subscriber_opens.distinct().count()

    human_subscriber_clicks = clicks_qs.filter(automated=False).values("subscriber")
    total_human_clicks = human_subscriber_clicks.count()
    human_clicks = human_subscriber_clicks.distinct().count()

    automated_opens = max(total_opens - total_human_opens, 0)
    automated_clicks = max(total_clicks - total_human_clicks, 0)
    automated_open_share = f"{str(round(automated_opens/total_opens*100))}%" if total_opens else "0%"
    automated_click_share = f"{str(round(automated_clicks/total_clicks*100))}%" if total_clicks else "0%"

    open_rate = f"{str(round(opens/newsletter.emails_sent*100))}%" if newsletter.emails_sent else "0"
    click_through_rate = f"{str(round(clicks/opens*100))}%" if opens else "0"
    human_open_rate = f"{str(round(human_opens/newsletter.emails_sent*100))}%" if newsletter.emails_sent else "0"
    human_click_through_rate = f"{str(round(human_clicks/human_opens*100))}%" if human_opens else "0"

    context = {
        "newsletter": newsletter,
        "newsletters_sent": newsletter.emails_sent,
        "total_opens": total_opens,
        "opens": opens,
        "total_clicks": total_clicks,
        "clicks": clicks,
        "open_rate": open_rate,
        "click_through_rate": click_through_rate,
        "total_human_opens": total_human_opens,
        "human_opens": human_opens,
        "total_human_clicks": total_human_clicks,
        "human_clicks": human_clicks,
        "human_open_rate": human_open_rate,
        "human_click_through_rate": human_click_through_rate,
        "automated_opens": automated_opens,
        "automated_clicks": automated_clicks,
        "automated_open_share": automated_open_share,
        "automated_click_share": automated_click_share,
    }

    template = "backend/newsletter_stats_detail.html"

    return render(request, template, context)


def _issue_builder_base_context(issue=None, review_form=None, form_action=None, is_edit=False):
    issue_qs = Issue.objects.prefetch_related("reviews__article", "reviews__author").order_by("-modified")
    context = {
        "issues": issue_qs[:25],
        "selected_issue": issue,
        "issue_form": IssueBuilderIssueForm(instance=issue) if issue else IssueBuilderIssueForm(),
        "max_featured_reviews": int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2)),
    }

    if issue:
        context["review_form"] = review_form or IssueBuilderReviewForm(issue=issue)
        context["review_form_action"] = form_action or reverse(
            "backend:add_issue_review",
            kwargs={"issue_id": issue.pk},
        )
        context["review_form_is_edit"] = is_edit

    return context


def _render_issue_panel(request, issue, review_form=None, form_action=None, is_edit=False):
    context = _issue_builder_base_context(
        issue=issue,
        review_form=review_form,
        form_action=form_action,
        is_edit=is_edit,
    )
    return render(request, "backend/issue_builder/_issue_reviews_panel.html", context)


def _validate_issue_publish(issue):
    errors = []
    max_featured = int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2))
    reviews = issue.reviews.select_related("article", "author").all()

    if not issue.name or not issue.body:
        errors.append("Issue requires a title and body before publishing.")

    if not reviews.exists():
        errors.append("Add at least one review before publishing.")

    featured_count = reviews.filter(is_featured=True).count()
    if featured_count > max_featured:
        errors.append(f"Only {max_featured} featured reviews are allowed.")

    for review in reviews:
        if not review.article_id:
            errors.append(f"Review {review.pk} is missing an article.")
        if not review.author_id:
            errors.append(f"Review {review.pk} is missing an author.")
        if not review.body:
            errors.append(f"Review {review.pk} is missing body content.")

    return errors


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def issue_builder(request):
    issue_id = request.GET.get("issue")
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None

    context = _issue_builder_base_context(issue=issue)
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def save_issue_draft(request, issue_id=None):
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None
    form = IssueBuilderIssueForm(request.POST, instance=issue)

    if form.is_valid():
        issue = form.save(commit=False)
        if not issue.pk:
            issue.active = False
        issue.save()
        messages.success(request, "Issue draft saved.")
        return_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}"
        if request.headers.get("HX-Request") == "true":
            response = _render_issue_panel(request, issue)
            response["HX-Redirect"] = return_url
            return response
        return redirect(return_url)

    context = _issue_builder_base_context(issue=issue)
    context["issue_form"] = form
    return render(request, "backend/issue_builder/issue_builder.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def new_review_form(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(issue=issue)
    return render(
        request,
        "backend/issue_builder/_review_form.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse("backend:add_issue_review", kwargs={"issue_id": issue.pk}),
            "is_edit": False,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def add_issue_review(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue)

    if form.is_valid():
        form.save()
        messages.success(request, "Review added to issue draft.")
        return _render_issue_panel(request, issue)

    return _render_issue_panel(request, issue, review_form=form)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def edit_issue_review_form(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(issue=issue, review=review)

    return render(
        request,
        "backend/issue_builder/_review_form.html",
        {
            "selected_issue": issue,
            "review_form": form,
            "form_action": reverse(
                "backend:update_issue_review",
                kwargs={"issue_id": issue.pk, "review_id": review.pk},
            ),
            "is_edit": True,
        },
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def update_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    form = IssueBuilderReviewForm(request.POST, request.FILES, issue=issue, review=review)

    if form.is_valid():
        form.save()
        messages.success(request, "Review updated.")
        return _render_issue_panel(request, issue)

    return _render_issue_panel(
        request,
        issue,
        review_form=form,
        form_action=reverse("backend:update_issue_review", kwargs={"issue_id": issue.pk, "review_id": review.pk}),
        is_edit=True,
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def remove_issue_review(request, issue_id, review_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    review = get_object_or_404(issue.reviews, pk=review_id)
    issue.reviews.remove(review)
    messages.success(request, "Review removed from issue.")

    return _render_issue_panel(request, issue)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def publish_issue_bundle(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    errors = _validate_issue_publish(issue)

    if errors:
        for error in errors:
            messages.error(request, error)
        return _render_issue_panel(request, issue)

    with transaction.atomic():
        issue.active = True
        issue.save(update_fields=["active", "modified"])

        reviews = issue.reviews.select_related("article").all()
        for review in reviews:
            if not review.article.active:
                review.article.active = True
                review.article.save(update_fields=["active", "modified"])
            if not review.active:
                review.active = True
                review.save()

        transaction.on_commit(bump_content_cache_version)

    messages.success(request, "Issue, reviews, and articles are now live.")
    return _render_issue_panel(request, issue)
