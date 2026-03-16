import datetime
import hashlib
import io
import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import MultipleObjectsReturned
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.newsletter.tasks import send_newsletter, send_newsletter_test_email
from spanza_journal_watch.submissions.models import Article, Author, Issue, Journal, Review
from spanza_journal_watch.utils.cache import bump_content_cache_version

from .forms import (
    HeaderForm,
    IssueBuilderIssueForm,
    IssueBuilderReviewForm,
    NewsletterCreateForm,
    NewsletterTestSendForm,
    PlankaApiKeyForm,
    PlankaProjectBackgroundForm,
    PlankaProjectNameForm,
    PlankaProjectSetupForm,
    SubscriberCSVForm,
    peek_csv,
)
from .models import (
    PlankaBoardBackgroundAsset,
    PlankaCardImport,
    PlankaIntegrationCredential,
    PlankaIssueBinding,
    SubscriberCSV,
)
from .planka import PlankaAPIError, PlankaClient
from .tasks import process_subscriber_csv

PLANKA_LIST_ORDER = [
    "articles",
    "candidates",
    "under_review",
    "publish_ready",
]

PLANKA_LIST_LABELS = {
    "articles": "Articles",
    "candidates": "Candidates",
    "under_review": "Under review",
    "publish_ready": "Publish ready",
}

PLANKA_INSTRUCTIONS_LIST_ORDER = ["reviewers", "editors", "administrators"]

PLANKA_INSTRUCTIONS_LIST_LABELS = {
    "reviewers": "Reviewers",
    "editors": "Editors",
    "administrators": "Administrators",
}

PLANKA_INSTRUCTIONS_DIR = Path(__file__).resolve().parent / "planka_instructions"

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


def _parse_instruction_cards(markdown_text):
    cards = []
    current_title = None
    current_body = []

    for line in (markdown_text or "").splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            if current_title:
                cards.append({"title": current_title, "body": "\n".join(current_body).strip()})
            current_title = heading_match.group(1).strip()
            current_body = []
            continue
        current_body.append(line)

    if current_title:
        cards.append({"title": current_title, "body": "\n".join(current_body).strip()})

    return [card for card in cards if card["title"]]


def _load_instruction_cards_by_bucket():
    cards_by_bucket = {}
    for bucket in PLANKA_INSTRUCTIONS_LIST_ORDER:
        path = PLANKA_INSTRUCTIONS_DIR / f"{bucket}.md"
        if not path.exists():
            cards_by_bucket[bucket] = []
            continue
        cards_by_bucket[bucket] = _parse_instruction_cards(path.read_text(encoding="utf-8"))

    return cards_by_bucket


def _normalize_background_to_webp(uploaded_file):
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image = image.convert("RGB")
            image.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=90, method=6)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Uploaded file is not a valid image.") from error


def _resolve_background_asset(form, user):
    selected_asset = form.cleaned_data.get("background_asset")
    uploaded_file = form.cleaned_data.get("background_upload")

    if uploaded_file:
        webp_bytes = _normalize_background_to_webp(uploaded_file)
        filename_slug = slugify(Path(uploaded_file.name).stem) or "background"
        asset = PlankaBoardBackgroundAsset(
            name=f"{filename_slug} ({timezone.now().strftime('%Y-%m-%d %H:%M')})",
            uploaded_by=user,
        )
        asset.image.save(
            f"{filename_slug}-{timezone.now().strftime('%Y%m%d%H%M%S')}.webp", ContentFile(webp_bytes), save=True
        )
        return asset

    return selected_asset


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

    summary = None
    if subscriber_csv.is_ready_to_process:
        try:
            summary = process_subscriber_csv(subscriber_csv.pk)
            messages.success(request, "Subscriber import complete.")
        except Exception as error:
            messages.error(request, f"Subscriber import failed: {_safe_planka_error(error)}")

    return render(request, "backend/process_csv_success.html", {"summary": summary})


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def dashboard(request):
    return render(request, "backend/dashboard.html")


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
def subscriber_list(request):
    query = (request.GET.get("q") or "").strip()
    subscribed = (request.GET.get("subscribed") or "").strip()
    bounced = (request.GET.get("bounced") or "").strip()
    complained = (request.GET.get("complained") or "").strip()

    subscribers = Subscriber.objects.select_related("from_csv").order_by("-modified", "-pk")
    if query:
        subscribers = subscribers.filter(Q(email__icontains=query) | Q(from_csv__name__icontains=query))
    if subscribed in {"true", "false"}:
        subscribers = subscribers.filter(subscribed=(subscribed == "true"))
    if bounced in {"true", "false"}:
        subscribers = subscribers.filter(bounced=(bounced == "true"))
    if complained in {"true", "false"}:
        subscribers = subscribers.filter(complained=(complained == "true"))

    context = {
        "subscribers": subscribers[:300],
        "subscriber_filters": {
            "q": query,
            "subscribed": subscribed,
            "bounced": bounced,
            "complained": complained,
        },
        "subscriber_total": subscribers.count(),
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "backend/_subscriber_list_results.html", context)

    return render(request, "backend/subscriber_list.html", context)


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


def _safe_planka_error(error):
    text = str(error or "").strip()
    if not text:
        return "Planka request failed."

    redacted = re.sub(r"(?i)(authorization\s*[:=]\s*)(bearer\s+[^\s,;]+)", r"\1[REDACTED]", text)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(password\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(token\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", redacted)

    return redacted[:500]


def _is_planka_connection_error(error):
    text = _safe_planka_error(error).lower()
    if not text:
        return False

    indicators = (
        "could not connect to planka",
        "connection refused",
        "failed to establish a new connection",
        "name or service not known",
        "temporary failure in name resolution",
        "timed out",
        "max retries exceeded",
        "connection aborted",
        "connection reset",
    )
    return any(marker in text for marker in indicators)


def _get_planka_integration_credential():
    return PlankaIntegrationCredential.get_solo()


def _build_planka_client():
    credential = _get_planka_integration_credential()
    if credential and credential.api_key:
        client = PlankaClient(api_key=credential.get_api_key(), access_token="")
    else:
        client = PlankaClient()

    if not client.configured:
        raise PlankaAPIError(
            "Planka is not configured. Add integration credentials or set PLANKA_BASE_URL and PLANKA_API_KEY."
        )

    return client


def _extract_publish_cards(binding):
    client = _build_planka_client()
    _, included = client.get_board(binding.board_id)

    lists = included.get("lists", []) or []
    cards = included.get("cards", []) or []
    custom_fields = included.get("customFields", []) or []
    custom_field_values = included.get("customFieldValues", []) or []

    publish_list_id = binding.get_list_id("publish_ready")
    if not publish_list_id:
        publish_list = next(
            (item for item in lists if str(item.get("name", "")).strip().lower() in {"publish ready", "publish"}),
            None,
        )
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

    imports_by_card = {item.card_id: item for item in binding.imports.select_related("review").all()}
    publish_cards = []
    for card in cards:
        if publish_list_id and card.get("listId") != publish_list_id:
            continue

        card_schema = values_by_card.get(card.get("id"), {})
        missing_required = [
            field["label"] for field in PLANKA_SCHEMA_FIELDS if field["required"] and not card_schema.get(field["key"])
        ]

        card_id = card.get("id")
        existing_sync = imports_by_card.get(card_id)
        already_synced = existing_sync is not None
        sync_blocked_reason = ""
        if existing_sync and existing_sync.review and existing_sync.last_review_modified_at:
            if existing_sync.review.modified > existing_sync.last_review_modified_at:
                sync_blocked_reason = "Review has local edits since last sync."

        publish_cards.append(
            {
                "id": card_id,
                "name": card.get("name") or "(Untitled card)",
                "schema": card_schema,
                "missing_required": missing_required,
                "is_valid": not missing_required,
                "already_imported": already_synced,
                "sync_blocked_reason": sync_blocked_reason,
            }
        )

    return sorted(publish_cards, key=lambda item: item["name"].lower())


def _build_card_payload_hash(selected_card):
    payload = {
        "id": selected_card.get("id"),
        "name": selected_card.get("name"),
        "schema": selected_card.get("schema") or {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_planka_publish_summary(publish_cards):
    cards = publish_cards or []
    total_cards = len(cards)
    valid_cards = sum(1 for card in cards if card.get("is_valid") and not card.get("already_imported"))
    missing_cards = sum(1 for card in cards if not card.get("is_valid"))
    already_imported_cards = sum(1 for card in cards if card.get("already_imported"))
    return {
        "total": total_cards,
        "valid": valid_cards,
        "missing": missing_cards,
        "already_imported": already_imported_cards,
    }


def _render_planka_panel(
    request,
    issue,
    publish_cards=None,
    panel_status=None,
    panel_status_level="info",
    planka_disconnected=False,
):
    cards = publish_cards if publish_cards is not None else []
    context = _issue_builder_base_context(
        issue=issue,
        planka_publish_cards=cards,
        planka_publish_summary=_build_planka_publish_summary(cards),
        planka_panel_status=panel_status,
        planka_panel_status_level=panel_status_level,
        planka_disconnected=planka_disconnected,
    )
    return render(request, "backend/issue_builder/_planka_panel.html", context)


def _render_planka_project_context_card(request, issue, card_status=None, card_status_level="info"):
    context = _issue_builder_base_context(
        issue=issue,
        planka_context_status=card_status,
        planka_context_status_level=card_status_level,
    )
    return render(request, "backend/issue_builder/_planka_project_context_card.html", context)


def _issue_builder_base_context(
    issue=None,
    review_form=None,
    form_action=None,
    is_edit=False,
    planka_publish_cards=None,
    planka_publish_summary=None,
    planka_panel_status=None,
    planka_panel_status_level="info",
    planka_api_key_form=None,
    planka_background_form=None,
    planka_project_name_form=None,
    planka_context_status=None,
    planka_context_status_level="info",
    planka_disconnected=False,
):
    issue_qs = Issue.objects.prefetch_related("reviews__article", "reviews__author").order_by("-modified")
    credential = _get_planka_integration_credential()
    background_assets = PlankaBoardBackgroundAsset.objects.order_by("name")
    context = {
        "issues": issue_qs[:25],
        "selected_issue": issue,
        "issue_form": IssueBuilderIssueForm(instance=issue) if issue else IssueBuilderIssueForm(),
        "max_featured_reviews": int(getattr(settings, "ISSUE_BUILDER_MAX_FEATURED_REVIEWS", 2)),
        "planka_credential": credential,
        "planka_api_key_form": planka_api_key_form or PlankaApiKeyForm(),
        "planka_binding": None,
        "planka_setup_form": PlankaProjectSetupForm(),
        "planka_background_form": planka_background_form or PlankaProjectBackgroundForm(),
        "planka_project_name_form": planka_project_name_form or PlankaProjectNameForm(),
        "planka_background_assets": background_assets,
        "planka_publish_cards": planka_publish_cards,
        "planka_publish_summary": planka_publish_summary,
        "planka_panel_status": planka_panel_status,
        "planka_panel_status_level": planka_panel_status_level,
        "planka_disconnected": planka_disconnected,
        "planka_context_status": planka_context_status,
        "planka_context_status_level": planka_context_status_level,
    }

    if issue:
        binding = PlankaIssueBinding.objects.filter(issue=issue).first()
        context["planka_binding"] = binding
        context["planka_setup_form"] = PlankaProjectSetupForm(initial={"project_name": issue.name})
        if binding and binding.background_asset_id:
            context["planka_background_form"].fields["background_asset"].initial = binding.background_asset_id
        if binding:
            context["planka_project_name_form"].fields["project_name"].initial = binding.project_name

        context["review_form"] = review_form or IssueBuilderReviewForm(issue=issue)
        context["review_form_action"] = form_action or reverse(
            "backend:add_issue_review",
            kwargs={"issue_id": issue.pk},
        )
        context["review_form_is_edit"] = is_edit

        if context["planka_publish_cards"] is None:
            context["planka_publish_cards"] = []
        if context["planka_publish_summary"] is None:
            context["planka_publish_summary"] = _build_planka_publish_summary(context["planka_publish_cards"])

        context["planka_api_key_form"].fields["issue_id"].initial = issue.pk

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
def issue_planka_import(request):
    issue_id = request.GET.get("issue")
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None

    context = _issue_builder_base_context(issue=issue)
    binding = context.get("planka_binding")
    if issue and binding:
        try:
            publish_cards = _extract_publish_cards(binding)
            context["planka_publish_cards"] = publish_cards
            context["planka_publish_summary"] = _build_planka_publish_summary(publish_cards)
            if request.GET.get("refresh") == "1":
                summary = context["planka_publish_summary"]
                context["planka_panel_status"] = (
                    f"Refresh complete. {summary['total']} publish cards loaded "
                    f"({summary['valid']} ready, {summary['missing']} with missing fields, "
                    f"{summary['already_imported']} already synced)."
                )
                context["planka_panel_status_level"] = "success"
        except PlankaAPIError as error:
            safe_error = _safe_planka_error(error)
            context["planka_publish_cards"] = []
            context["planka_publish_summary"] = _build_planka_publish_summary([])
            if _is_planka_connection_error(error):
                context["planka_panel_status"] = "Not connected to Planka. Retrying in background…"
                context["planka_disconnected"] = True
            else:
                context["planka_panel_status"] = f"Could not refresh Planka cards: {safe_error}"
            context["planka_panel_status_level"] = "danger"

    return render(request, "backend/issue_builder/planka_import.html", context)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_save_api_key(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    form = PlankaApiKeyForm(request.POST)
    issue_id = (request.POST.get("issue_id") or "").strip()
    issue = get_object_or_404(Issue, pk=issue_id) if issue_id else None

    if not form.is_valid():
        messages.error(request, "Please provide a valid Planka API key.")
        context = _issue_builder_base_context(
            issue=issue,
            planka_api_key_form=form,
        )
        return render(request, "backend/issue_builder/planka_import.html", context)

    api_key = form.cleaned_data["api_key"]
    try:
        validation_client = PlankaClient(api_key=api_key, access_token="")
        validation_client.get_current_user()

        credential = _get_planka_integration_credential() or PlankaIntegrationCredential(singleton=1)
        credential.auth_mode = PlankaIntegrationCredential.AuthMode.API_KEY
        credential.set_api_key(api_key)
        credential.api_key_prefix = api_key.split("_", 1)[0] if "_" in api_key else ""
        credential.configured_by = request.user
        credential.last_validated_at = timezone.now()
        credential.last_error = ""
        credential.save()
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        credential = _get_planka_integration_credential()
        if credential:
            credential.last_error = safe_error
            credential.save(update_fields=["last_error", "modified"])

        messages.error(request, f"Could not validate Planka API key: {safe_error}")
        context = _issue_builder_base_context(
            issue=issue,
            planka_api_key_form=form,
        )
        return render(request, "backend/issue_builder/planka_import.html", context)

    messages.success(request, "Planka API key saved successfully.")
    redirect_url = reverse("backend:issue_planka_import")
    if issue:
        redirect_url = f"{redirect_url}?issue={issue.pk}"
    return redirect(redirect_url)


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
        return _render_planka_panel(
            request,
            issue,
            panel_status="This issue is already linked to a Planka project.",
            panel_status_level="info",
        )

    form = PlankaProjectSetupForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_planka_panel(
            request,
            issue,
            panel_status="Project name is required.",
            panel_status_level="danger",
        )

    project_name = form.cleaned_data["project_name"]
    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        return _render_planka_panel(
            request,
            issue,
            panel_status=str(error),
            panel_status_level="danger",
        )
    instruction_cards = _load_instruction_cards_by_bucket()

    try:
        client = _build_planka_client()
        project = client.create_project(project_name)

        if background_asset:
            with background_asset.image.open("rb") as image_file:
                background_image = client.upload_project_background_image(
                    project["id"],
                    image_file,
                    filename=Path(background_asset.image.name).name,
                    content_type="image/webp",
                )
            background_image_id = background_image.get("id")
            if background_image_id:
                client.update_project_background(
                    project["id"],
                    background_type="image",
                    background_image_id=background_image_id,
                )

        board = client.create_board(project["id"], name="Reviews")
        instructions_board = client.create_board(project["id"], name="Instructions", position=2 * 65536)

        list_mapping = {}
        for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
            list_obj = client.create_list(
                board_id=board["id"],
                name=PLANKA_LIST_LABELS[key],
                position=index * 65536,
                list_type="active",
            )
            list_mapping[key] = list_obj["id"]

        instruction_list_mapping = {}
        for index, key in enumerate(PLANKA_INSTRUCTIONS_LIST_ORDER, start=1):
            list_obj = client.create_list(
                board_id=instructions_board["id"],
                name=PLANKA_INSTRUCTIONS_LIST_LABELS[key],
                position=index * 65536,
                list_type="active",
            )
            instruction_list_mapping[key] = list_obj["id"]

            cards_for_list = instruction_cards.get(key, [])
            for card_index, card in enumerate(cards_for_list, start=1):
                client.create_card(
                    list_id=list_obj["id"],
                    name=card["title"],
                    description=card["body"],
                    position=card_index * 65536,
                    card_type="story",
                )

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
        return _render_planka_panel(
            request,
            issue,
            panel_status=f"Unable to set up Planka project: {error}",
            panel_status_level="danger",
        )

    PlankaIssueBinding.objects.create(
        issue=issue,
        project_id=project["id"],
        project_name=project_name,
        board_id=board["id"],
        board_name=board.get("name") or "Reviews",
        instructions_board_id=instructions_board["id"],
        instructions_board_name=instructions_board.get("name") or "Instructions",
        lists=list_mapping,
        instructions_lists=instruction_list_mapping,
        custom_fields=custom_fields,
        custom_field_group_id=field_group["id"],
        background_asset=background_asset,
    )

    return _render_planka_panel(
        request,
        issue,
        panel_status="Planka project linked to this issue.",
        panel_status_level="success",
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_update_project_name(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectNameForm(request.POST)
    redirect_url = f"{reverse('backend:issue_planka_import')}?issue={issue.pk}"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please enter a valid project name.",
                card_status_level="danger",
            )
        messages.error(request, "Please enter a valid project name.")
        return redirect(redirect_url)

    project_name = form.cleaned_data["project_name"]

    try:
        client = _build_planka_client()
        client.update_project_name(binding.project_id, project_name)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not rename project: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not rename project: {safe_error}")
        return redirect(redirect_url)

    binding.project_name = project_name
    binding.save(update_fields=["project_name", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Project name updated.",
            card_status_level="success",
        )

    messages.success(request, "Project name updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_update_project_background(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectBackgroundForm(request.POST, request.FILES)
    redirect_url = f"{reverse('backend:issue_planka_import')}?issue={issue.pk}"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please fix background image form errors.",
                card_status_level="danger",
            )
        messages.error(request, "Please fix background image form errors.")
        return redirect(redirect_url)

    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=str(error),
                card_status_level="danger",
            )
        messages.error(request, str(error))
        return redirect(redirect_url)

    if not background_asset:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Select or upload a background image first.",
                card_status_level="info",
            )
        messages.info(request, "Select or upload a background image first.")
        return redirect(redirect_url)

    try:
        client = _build_planka_client()
        with background_asset.image.open("rb") as image_file:
            background_image = client.upload_project_background_image(
                binding.project_id,
                image_file,
                filename=Path(background_asset.image.name).name,
                content_type="image/webp",
            )

        background_image_id = background_image.get("id")
        if not background_image_id:
            raise PlankaAPIError("Planka did not return a background image id.")

        client.update_project_background(
            binding.project_id,
            background_type="image",
            background_image_id=background_image_id,
        )
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not update background image: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not update background image: {safe_error}")
        return redirect(redirect_url)

    binding.background_asset = background_asset
    binding.save(update_fields=["background_asset", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Background image updated.",
            card_status_level="success",
        )

    messages.success(request, "Background image updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_refresh_publish_cards(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)

    try:
        publish_cards = _extract_publish_cards(binding)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=[],
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else f"Could not refresh Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
        )

    summary = _build_planka_publish_summary(publish_cards)
    return _render_planka_panel(
        request,
        issue,
        publish_cards=publish_cards,
        panel_status=(
            f"Refresh complete. {summary['total']} publish cards loaded "
            f"({summary['valid']} ready, {summary['missing']} with missing fields, "
            f"{summary['already_imported']} already synced)."
        ),
        panel_status_level="success",
    )


@login_required
@permission_required("submissions.manage_issue_builder", raise_exception=True)
def planka_import_publish_card(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_id = (request.POST.get("card_id") or "").strip()
    if not card_id:
        return _render_planka_panel(
            request,
            issue,
            panel_status="Card id missing.",
            panel_status_level="danger",
        )

    existing_sync = (
        PlankaCardImport.objects.filter(card_id=card_id).select_related("review", "review__article").first()
    )

    try:
        publish_cards = _extract_publish_cards(binding)

        selected = next((item for item in publish_cards if item["id"] == card_id), None)
        if not selected:
            return _render_planka_panel(
                request,
                issue,
                publish_cards=publish_cards,
                panel_status="Card not found in Publish ready list.",
                panel_status_level="danger",
            )

        if selected.get("sync_blocked_reason"):
            return _render_planka_panel(
                request,
                issue,
                publish_cards=publish_cards,
                panel_status=selected["sync_blocked_reason"],
                panel_status_level="danger",
            )

        schema = selected["schema"]
        article_url = (schema.get("article_url") or "").strip()

        journal = None
        journal_name = (schema.get("journal_name") or "").strip()
        if journal_name:
            journal, _ = Journal.objects.get_or_create(name=journal_name)

        try:
            article_year = int(schema.get("article_year") or datetime.date.today().year)
        except ValueError:
            article_year = datetime.date.today().year

        review = existing_sync.review if existing_sync and existing_sync.review_id else None
        article = review.article if review and review.article_id else None
        if not article and article_url:
            article = Article.objects.filter(url=article_url).first()
        if not article:
            article = Article.objects.create(
                name=(schema.get("article_name") or selected["name"] or "Untitled article").strip(),
                journal=journal,
                year=article_year,
                citation=(schema.get("article_citation") or "").strip(),
                url=article_url or None,
                tags_string=(schema.get("tags_string") or "").strip(),
                active=False,
            )
        else:
            article.name = (
                schema.get("article_name") or article.name or selected["name"] or "Untitled article"
            ).strip()
            if journal is not None:
                article.journal = journal
            article.year = article_year
            if schema.get("article_citation") is not None:
                article.citation = schema.get("article_citation") or ""
            if article_url:
                article.url = article_url
            if schema.get("tags_string") is not None:
                article.tags_string = schema.get("tags_string") or ""
            article.save()

        author_name = (schema.get("author_name") or "").strip()
        author = review.author if review and review.author_id else None
        if author_name:
            author_matches = Author.objects.filter(name=author_name)
            if author_matches.count() == 1:
                author = author_matches.first()
            elif author_matches.count() == 0:
                author = Author.objects.create(
                    title=(schema.get("author_title") or "Dr").strip() or "Dr",
                    name=author_name,
                )
            else:
                messages.warning(
                    request,
                    "Multiple matching authors found. Existing review author retained for sync.",
                )

        if not review:
            review = Review.objects.create(
                article=article,
                author=author,
                body=schema.get("review_body_markdown") or "",
                is_featured=_bool_from_value(schema.get("is_featured")),
                active=False,
            )
            issue.reviews.add(review)
        else:
            review.article = article
            if author is not None:
                review.author = author
            if schema.get("review_body_markdown") is not None:
                review.body = schema.get("review_body_markdown") or ""
            if schema.get("is_featured") is not None:
                review.is_featured = _bool_from_value(schema.get("is_featured"))
            review.save()

        card_payload_hash = _build_card_payload_hash(selected)
        if existing_sync:
            existing_sync.issue = issue
            existing_sync.binding = binding
            existing_sync.card_name = selected["name"]
            existing_sync.review = review
            existing_sync.imported_by = request.user
            existing_sync.last_card_payload_hash = card_payload_hash
            existing_sync.last_review_modified_at = review.modified
            existing_sync.save()
        else:
            PlankaCardImport.objects.create(
                issue=issue,
                binding=binding,
                card_id=card_id,
                card_name=selected["name"],
                review=review,
                imported_by=request.user,
                last_card_payload_hash=card_payload_hash,
                last_review_modified_at=review.modified,
            )

        missing_required = selected.get("missing_required") or []
        if missing_required:
            panel_status = (
                "Review synced with missing fields: "
                + ", ".join(missing_required)
                + ". You can fill remaining data and sync again later."
            )
            panel_level = "warning"
        else:
            panel_status = "Review synced from Planka into this issue draft."
            panel_level = "success"

        try:
            refreshed_cards = _extract_publish_cards(binding)
        except PlankaAPIError as error:
            refreshed_cards = []
            panel_status = f"{panel_status} Could not refresh cards after sync: {error}"
            panel_level = "warning"

        return _render_planka_panel(
            request,
            issue,
            publish_cards=refreshed_cards,
            panel_status=panel_status,
            panel_status_level=panel_level,
        )

    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        return _render_planka_panel(
            request,
            issue,
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else f"Could not fetch Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
        )
    except Exception as error:
        return _render_planka_panel(
            request,
            issue,
            panel_status=f"Sync failed: {_safe_planka_error(error)}",
            panel_status_level="danger",
        )
