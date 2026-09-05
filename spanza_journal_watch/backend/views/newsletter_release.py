"""Newsletter composition, test sends, final send and stats."""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.newsletter.tasks import send_newsletter, send_newsletter_test_email

from ..forms import (
    NewsletterCreateForm,
    NewsletterEditForm,
    NewsletterTestSendForm,
)
from .analytics_page import _newsletter_predates_site_analytics, _safe_percentage, _site_analytics_rollout_date
from .shared import _resolve_and_persist_issue

logger = logging.getLogger(__name__)


def _copy_issue_image_to_newsletter(newsletter, issue):
    """Copy issue.image into newsletter.header_image, triggering image processing."""
    import logging as _logging
    import os as _os

    from django.core.files.base import ContentFile as _ContentFile

    if not (issue and issue.image and issue.image.name):
        return False
    try:
        issue.image.open("rb")
        content = issue.image.read()
        issue.image.close()
        filename = _os.path.basename(issue.image.name)
        newsletter.header_image_processed = False
        newsletter.save(update_fields=["header_image_processed"])
        newsletter.header_image.save(filename, _ContentFile(content), save=True)
        return True
    except (OSError, ValueError) as exc:
        _logging.getLogger(__name__).warning("Could not copy issue image to newsletter: %s", exc)
        return False


def _newsletter_ready_to_send(newsletter):
    return bool(
        newsletter.content_heading and newsletter.content and newsletter.header_image and newsletter.header_image.name
    )


def _newsletter_stats(newsletter):
    from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen

    opens_qs = NewsletterOpen.objects.filter(newsletter=newsletter)
    clicks_qs = NewsletterClick.objects.filter(newsletter=newsletter)
    total_opens = opens_qs.values("subscriber").count()
    opens = opens_qs.values("subscriber").distinct().count()
    total_clicks = clicks_qs.values("subscriber").count()
    clicks = clicks_qs.values("subscriber").distinct().count()
    human_opens_qs = opens_qs.filter(automated=False)
    human_clicks_qs = clicks_qs.filter(automated=False)
    total_human_opens = human_opens_qs.values("subscriber").count()
    human_opens = human_opens_qs.values("subscriber").distinct().count()
    total_human_clicks = human_clicks_qs.values("subscriber").count()
    human_clicks = human_clicks_qs.values("subscriber").distinct().count()
    automated_opens = max(total_opens - total_human_opens, 0)
    automated_clicks = max(total_clicks - total_human_clicks, 0)
    return {
        "emails_sent": newsletter.emails_sent,
        "opens": opens,
        "total_opens": total_opens,
        "clicks": clicks,
        "total_clicks": total_clicks,
        "human_opens": human_opens,
        "total_human_opens": total_human_opens,
        "human_clicks": human_clicks,
        "total_human_clicks": total_human_clicks,
        "automated_opens": automated_opens,
        "automated_clicks": automated_clicks,
        "automated_open_share": f"{round(automated_opens / total_opens * 100)}%" if total_opens else "0%",
        "automated_click_share": f"{round(automated_clicks / total_clicks * 100)}%" if total_clicks else "0%",
        "open_rate": f"{round(opens / newsletter.emails_sent * 100)}%" if newsletter.emails_sent else "—",
        "human_open_rate": f"{round(human_opens / newsletter.emails_sent * 100)}%" if newsletter.emails_sent else "—",
        "click_through_rate": f"{round(clicks / opens * 100)}%" if opens else "—",
        "human_click_through_rate": f"{round(human_clicks / human_opens * 100)}%" if human_opens else "—",
    }


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def newsletter_release_list(request):
    selected_issue = _resolve_and_persist_issue(request)
    newsletter = None
    edit_form = None

    if selected_issue:
        newsletter = Newsletter.objects.filter(issue=selected_issue).order_by("-pk").first()
        if not newsletter:
            newsletter = Newsletter.objects.create(
                issue=selected_issue,
                subject=f"Journal Watch \u2014 {selected_issue.name}",
                content=selected_issue.body or "",
            )

        if request.method == "POST":
            edit_form = NewsletterEditForm(request.POST, request.FILES, instance=newsletter)
            if edit_form.is_valid():
                new_image_uploaded = bool(request.FILES.get("header_image"))
                use_issue_image = edit_form.cleaned_data.get("use_issue_image")
                if new_image_uploaded:
                    edit_form.instance.header_image_processed = False
                newsletter = edit_form.save(commit=False)
                newsletter.ready_to_send = _newsletter_ready_to_send(newsletter)
                newsletter.save()
                if use_issue_image and not new_image_uploaded:
                    _copy_issue_image_to_newsletter(newsletter, selected_issue)
                    newsletter.refresh_from_db()
                    newsletter.ready_to_send = _newsletter_ready_to_send(newsletter)
                    newsletter.save(update_fields=["ready_to_send"])
                messages.success(request, "Newsletter saved.")
                return redirect(f"{reverse('backend:newsletter_release_list')}?issue={selected_issue.pk}")
        else:
            edit_form = NewsletterEditForm(instance=newsletter)

    newsletter_stats = _newsletter_stats(newsletter) if (newsletter and newsletter.is_sent) else None

    newsletter_header_image_url = None
    if newsletter and newsletter.header_image and newsletter.header_image.name:
        try:
            url = newsletter.header_image.url
            # In local dev, skip images whose files don't exist to avoid 404s
            if settings.DEBUG and not newsletter.header_image.storage.exists(newsletter.header_image.name):
                pass
            else:
                newsletter_header_image_url = url
        except Exception:
            pass

    context = {
        "selected_issue": selected_issue,
        "newsletter": newsletter,
        "newsletter_stats": newsletter_stats,
        "edit_form": edit_form,
        "test_form": NewsletterTestSendForm(),
        "issue_for_preview": selected_issue
        if (selected_issue and selected_issue.image and selected_issue.image.name)
        else None,
        "newsletter_header_image_url": newsletter_header_image_url,
    }
    return render(request, "backend/newsletter_release_list.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def create_newsletter(request):
    if request.method == "POST":
        form = NewsletterCreateForm(request.POST, request.FILES)
        if form.is_valid():
            newsletter = form.save()
            # If no custom image uploaded but "use issue image" is checked, copy issue.image
            if form.cleaned_data.get("use_issue_image") and not newsletter.header_image:
                _copy_issue_image_to_newsletter(newsletter, newsletter.issue)
            messages.success(request, f"Newsletter {newsletter} created.")
            return redirect(reverse("backend:newsletter_release_list") + f"?issue={newsletter.issue_id}")
    else:
        selected_issue = _resolve_and_persist_issue(request, fallback_latest=False)
        initial = (
            {"issue": selected_issue, "use_issue_image": bool(selected_issue and selected_issue.image)}
            if selected_issue
            else {}
        )
        form = NewsletterCreateForm(initial=initial)

    # Pass pre-selected issue image for the preview (only meaningful on initial GET)
    issue_for_preview = None
    if request.method == "GET":
        if selected_issue and selected_issue.image:
            issue_for_preview = selected_issue
    context = {"form": form, "issue_for_preview": issue_for_preview}
    return render(request, "backend/create_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def final_newsletter(request, pk):
    # Provides last check before sending
    newsletter = Newsletter.objects.filter(pk=pk).first()
    context = {
        "newsletter": newsletter,
        "test_form": NewsletterTestSendForm(),
    }
    return render(request, "backend/final_newsletter.html", context)


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_test_newsletter(request, pk):
    newsletter = Newsletter.objects.filter(pk=pk).first()
    if not newsletter:
        messages.error(request, "Newsletter not found.")
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

    return redirect(reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def enable_newsletter_resend(request, pk):
    newsletter = Newsletter.objects.filter(pk=pk).first()
    if not newsletter:
        messages.error(request, "Newsletter not found.")
        return redirect("backend:dashboard")

    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    if not newsletter.is_sent:
        messages.error(request, "This newsletter has not been sent yet.")
    else:
        newsletter.resend_enabled = True
        newsletter.save(update_fields=["resend_enabled"])
        messages.warning(request, "Resend enabled for this newsletter. It can now be sent once more.")

    return redirect(reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk}))


@login_required
@permission_required("backend.send_newsletters", raise_exception=True)  # Prevents login loop
def send_final_newsletter(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    try:
        newsletter = Newsletter.objects.get(pk=pk)
    except Newsletter.DoesNotExist:
        messages.error(request, "Newsletter not found. Please refresh and try again.")
        return redirect("backend:newsletter_release_list")

    if newsletter.is_ready_to_send():
        # Celery task re-checks and atomically claims the send. The stats
        # email goes back to the admin who triggered the send.
        send_newsletter.apply_async(
            (newsletter.pk,),
            {"sender_email": request.user.email or None},
            countdown=1,
        )
        messages.success(request, f"Newsletter {newsletter} queued for sending")
    elif newsletter.is_sent and not newsletter.resend_enabled:
        messages.warning(
            request,
            f"Newsletter {newsletter} has already been sent. Use 'Enable one resend' below to dispatch it again.",
        )
    else:
        messages.error(request, f"Newsletter {newsletter} not sent: not ready")

    return redirect(reverse("backend:final_newsletter", kwargs={"pk": newsletter.pk}))


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
    automated_open_share = f"{str(round(automated_opens / total_opens * 100))}%" if total_opens else "0%"
    automated_click_share = f"{str(round(automated_clicks / total_clicks * 100))}%" if total_clicks else "0%"

    open_rate = _safe_percentage(opens, newsletter.emails_sent)
    click_through_rate = _safe_percentage(clicks, newsletter.emails_sent)
    click_to_open_rate = _safe_percentage(clicks, opens)
    human_open_rate = _safe_percentage(human_opens, newsletter.emails_sent)
    human_click_through_rate = _safe_percentage(human_clicks, newsletter.emails_sent)
    human_click_to_open_rate = _safe_percentage(human_clicks, human_opens)
    partial_site_analytics = _newsletter_predates_site_analytics(newsletter)
    site_analytics_rollout_date = _site_analytics_rollout_date()

    context = {
        "newsletter": newsletter,
        "newsletters_sent": newsletter.emails_sent,
        "total_opens": total_opens,
        "opens": opens,
        "total_clicks": total_clicks,
        "clicks": clicks,
        "open_rate": open_rate,
        "click_through_rate": click_through_rate,
        "click_to_open_rate": click_to_open_rate,
        "total_human_opens": total_human_opens,
        "human_opens": human_opens,
        "total_human_clicks": total_human_clicks,
        "human_clicks": human_clicks,
        "human_open_rate": human_open_rate,
        "human_click_through_rate": human_click_through_rate,
        "human_click_to_open_rate": human_click_to_open_rate,
        "automated_opens": automated_opens,
        "automated_clicks": automated_clicks,
        "automated_open_share": automated_open_share,
        "automated_click_share": automated_click_share,
        "partial_site_analytics": partial_site_analytics,
        "site_analytics_rollout_date": site_analytics_rollout_date,
    }

    template = "backend/newsletter_stats_detail.html"

    return render(request, template, context)
