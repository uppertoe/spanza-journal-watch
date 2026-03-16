import datetime

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
from spanza_journal_watch.submissions.models import Article, Author, Issue, Journal, Review
from spanza_journal_watch.utils.cache import bump_content_cache_version

from .forms import (
    HeaderForm,
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    NewsletterCreateForm,
    NewsletterTestSendForm,
    PlankaProjectSetupForm,
    SubscriberCSVForm,
    peek_csv,
)
from .models import PlankaCardImport, PlankaIssueBinding, SubscriberCSV
from .planka import PlankaAPIError, PlankaClient
from .tasks import process_subscriber_csv

PLANKA_LIST_ORDER = [
    "instructions",
    "articles",
    "candidates",
    "under_review",
    "editing",
    "publish",
    "imported",
]

PLANKA_LIST_LABELS = {
    "instructions": "Instructions",
    "articles": "Articles",
    "candidates": "Candidates",
    "under_review": "Under review",
    "editing": "Editing",
    "publish": "Publish",
    "imported": "Imported",
}

PLANKA_SCHEMA_FIELDS = [
    {"key": "article_url", "label": "Article URL", "required": True, "show_on_front": True},
    {"key": "article_name", "label": "Article Name", "required": True, "show_on_front": True},
    {"key": "journal_name", "label": "Journal Name", "required": False, "show_on_front": False},
    {"key": "article_year", "label": "Article Year", "required": False, "show_on_front": False},
    {"key": "article_citation", "label": "Article Citation", "required": False, "show_on_front": False},
    {"key": "author_name", "label": "Author Name", "required": True, "show_on_front": True},
    {"key": "author_title", "label": "Author Title", "required": False, "show_on_front": False},
    {
        "key": "review_body_markdown",
        "label": "Review Body Markdown",
        "required": True,
        "show_on_front": False,
    },
    {"key": "is_featured", "label": "Is Featured", "required": False, "show_on_front": True},
    {"key": "tags_string", "label": "Tags", "required": False, "show_on_front": False},
]

PLANKA_FIELD_LABEL_TO_KEY = {item["label"].lower(): item["key"] for item in PLANKA_SCHEMA_FIELDS}


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


def _bool_from_value(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_planka_client():
    client = PlankaClient()
    if not client.configured:
        raise PlankaAPIError(
            "Planka is not configured. Set PLANKA_BASE_URL and PLANKA_API_KEY or PLANKA_ACCESS_TOKEN."
        )
    return client


def _extract_publish_cards(binding):
    client = _build_planka_client()
    _, included = client.get_board(binding.board_id)

    lists = included.get("lists", []) or []
    cards = included.get("cards", []) or []
    custom_fields = included.get("customFields", []) or []
    custom_field_values = included.get("customFieldValues", []) or []

    publish_list_id = binding.get_list_id("publish")
    if not publish_list_id:
        publish_list = next((item for item in lists if str(item.get("name", "")).strip().lower() == "publish"), None)
        publish_list_id = publish_list.get("id") if publish_list else None

    field_name_by_id = {
        item["id"]: str(item.get("name") or "").strip().lower() for item in custom_fields if item.get("id")
    }
    values_by_card = {}
    for value in custom_field_values:
        card_id = value.get("cardId")
        field_id = value.get("customFieldId")
        if not card_id or not field_id:
            continue
        field_label = field_name_by_id.get(field_id, "")
        field_key = PLANKA_FIELD_LABEL_TO_KEY.get(field_label)
        if not field_key:
            continue
        content = value.get("content")
        values_by_card.setdefault(card_id, {})[field_key] = str(content or "").strip()

    imported_card_ids = set(binding.imports.values_list("card_id", flat=True))
    publish_cards = []
    for card in cards:
        if publish_list_id and card.get("listId") != publish_list_id:
            continue

        card_schema = values_by_card.get(card.get("id"), {})
        missing_required = [
            field["label"] for field in PLANKA_SCHEMA_FIELDS if field["required"] and not card_schema.get(field["key"])
        ]

        publish_cards.append(
            {
                "id": card.get("id"),
                "name": card.get("name") or "(Untitled card)",
                "schema": card_schema,
                "missing_required": missing_required,
                "is_valid": not missing_required,
                "already_imported": card.get("id") in imported_card_ids,
            }
        )

    return sorted(publish_cards, key=lambda item: item["name"].lower())


def _render_planka_panel(request, issue, publish_cards=None):
    context = _issue_builder_base_context(issue=issue, planka_publish_cards=publish_cards)
    return render(request, "backend/issue_builder/_planka_panel.html", context)


def _issue_builder_base_context(
    issue=None,
    review_form=None,
    form_action=None,
    is_edit=False,
    planka_publish_cards=None,
):
    issue_qs = Issue.objects.prefetch_related("reviews__article", "reviews__author").order_by("-modified")
    context = {
        "issues": issue_qs[:25],
        "selected_issue": issue,
        "issue_form": IssueBuilderIssueForm(instance=issue) if issue else IssueBuilderIssueForm(),
        "max_featured_reviews": int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2)),
        "planka_binding": None,
        "planka_setup_form": PlankaProjectSetupForm(),
        "planka_publish_cards": planka_publish_cards,
    }

    if issue:
        binding = PlankaIssueBinding.objects.filter(issue=issue).first()
        context["planka_binding"] = binding
        context["planka_setup_form"] = PlankaProjectSetupForm(initial={"project_name": issue.name})

        context["review_form"] = review_form or IssueBuilderReviewForm(issue=issue)
        context["review_form_action"] = form_action or reverse(
            "backend:add_issue_review",
            kwargs={"issue_id": issue.pk},
        )
        context["review_form_is_edit"] = is_edit

        if context["planka_publish_cards"] is None:
            context["planka_publish_cards"] = []

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


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_setup_issue_project(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    if hasattr(issue, "planka_binding"):
        messages.info(request, "This issue is already linked to a Planka project.")
        return _render_planka_panel(request, issue)

    form = PlankaProjectSetupForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Project name is required.")
        return _render_planka_panel(request, issue)

    project_name = form.cleaned_data["project_name"]

    try:
        client = _build_planka_client()
        project = client.create_project(project_name)
        board = client.create_board(project["id"], name="Reviews")

        list_mapping = {}
        for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
            list_obj = client.create_list(
                board_id=board["id"],
                name=PLANKA_LIST_LABELS[key],
                position=index * 65536,
                list_type="active",
            )
            list_mapping[key] = list_obj["id"]

        field_group = client.create_custom_field_group(board["id"])
        custom_fields = {}
        for index, field in enumerate(PLANKA_SCHEMA_FIELDS, start=1):
            field_obj = client.create_custom_field(
                custom_field_group_id=field_group["id"],
                name=field["label"],
                position=index * 65536,
                show_on_front=field["show_on_front"],
            )
            custom_fields[field["key"]] = field_obj["id"]

    except (KeyError, PlankaAPIError) as error:
        messages.error(request, f"Unable to set up Planka project: {error}")
        return _render_planka_panel(request, issue)

    PlankaIssueBinding.objects.create(
        issue=issue,
        project_id=project["id"],
        project_name=project_name,
        board_id=board["id"],
        board_name=board.get("name") or "Reviews",
        lists=list_mapping,
        custom_fields=custom_fields,
        custom_field_group_id=field_group["id"],
    )

    messages.success(request, "Planka project linked to this issue.")
    return _render_planka_panel(request, issue)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_refresh_publish_cards(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)

    try:
        publish_cards = _extract_publish_cards(binding)
    except PlankaAPIError as error:
        messages.error(request, f"Could not refresh Planka cards: {error}")
        publish_cards = []

    return _render_planka_panel(request, issue, publish_cards=publish_cards)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_import_publish_card(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_id = (request.POST.get("card_id") or "").strip()
    if not card_id:
        messages.error(request, "Card id missing.")
        return _render_planka_panel(request, issue)

    if PlankaCardImport.objects.filter(card_id=card_id).exists():
        messages.info(request, "This card has already been imported.")
        try:
            publish_cards = _extract_publish_cards(binding)
        except PlankaAPIError as error:
            messages.warning(request, f"Card already imported, and refresh failed: {error}")
            publish_cards = []
        return _render_planka_panel(request, issue, publish_cards=publish_cards)

    try:
        publish_cards = _extract_publish_cards(binding)
    except PlankaAPIError as error:
        messages.error(request, f"Could not fetch Planka cards: {error}")
        return _render_planka_panel(request, issue)

    selected = next((item for item in publish_cards if item["id"] == card_id), None)
    if not selected:
        messages.error(request, "Card not found in Publish list.")
        return _render_planka_panel(request, issue, publish_cards=publish_cards)

    if not selected["is_valid"]:
        missing = ", ".join(selected["missing_required"])
        messages.error(request, f"Card is missing required fields: {missing}")
        return _render_planka_panel(request, issue, publish_cards=publish_cards)

    schema = selected["schema"]
    article_url = schema.get("article_url", "")

    journal = None
    journal_name = schema.get("journal_name", "")
    if journal_name:
        journal, _ = Journal.objects.get_or_create(name=journal_name)

    try:
        article_year = int(schema.get("article_year") or datetime.date.today().year)
    except ValueError:
        article_year = datetime.date.today().year

    article, _ = Article.objects.get_or_create(
        url=article_url,
        defaults={
            "name": schema.get("article_name") or selected["name"],
            "journal": journal,
            "year": article_year,
            "citation": schema.get("article_citation") or "",
            "tags_string": schema.get("tags_string") or "",
            "active": False,
        },
    )

    author_name = schema.get("author_name", "")
    author_matches = Author.objects.filter(name=author_name)
    author = None
    if author_matches.count() == 1:
        author = author_matches.first()
    elif author_matches.count() == 0:
        author = Author.objects.create(
            title=schema.get("author_title") or "Dr",
            name=author_name,
        )
    else:
        messages.warning(
            request,
            "Multiple matching authors found. Review author left blank for manual selection.",
        )

    review = Review.objects.create(
        article=article,
        author=author,
        body=schema.get("review_body_markdown") or "",
        is_featured=_bool_from_value(schema.get("is_featured")),
        active=False,
    )
    issue.reviews.add(review)

    PlankaCardImport.objects.create(
        issue=issue,
        binding=binding,
        card_id=card_id,
        card_name=selected["name"],
        review=review,
        imported_by=request.user,
    )

    imported_list_id = binding.get_list_id("imported")
    if imported_list_id:
        try:
            client = _build_planka_client()
            client.move_card(card_id, imported_list_id)
        except PlankaAPIError as error:
            messages.warning(request, f"Review imported, but card could not be moved to Imported: {error}")

    messages.success(request, "Review imported from Planka into this issue draft.")

    try:
        refreshed_cards = _extract_publish_cards(binding)
    except PlankaAPIError:
        refreshed_cards = []

    return _render_planka_panel(request, issue, publish_cards=refreshed_cards)
