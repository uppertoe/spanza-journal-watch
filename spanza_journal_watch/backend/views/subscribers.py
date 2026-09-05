"""Subscriber list, CSV upload and staff user toggles."""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import MultipleObjectsReturned
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from spanza_journal_watch.newsletter.models import Subscriber

from ..forms import (
    HeaderForm,
    SubscriberCSVForm,
    peek_csv,
)
from ..models import (
    SubscriberCSV,
)
from ..tasks import (
    process_subscriber_csv,
)

logger = logging.getLogger(__name__)


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
            logger.debug("CSV header set to: %s", header)
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
    subscriber_csv.task_state = SubscriberCSV.TASK_STATE_PENDING
    subscriber_csv.task_note = "Queued for processing..."
    subscriber_csv.task_summary = {}
    subscriber_csv.save()

    if subscriber_csv.is_ready_to_process():
        process_subscriber_csv.delay(subscriber_csv.pk)

    return render(
        request,
        "backend/process_csv_status.html",
        {"instance": subscriber_csv},
    )


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
def process_csv_status(request, save_token):
    """HTMX polling endpoint: returns current task state for a SubscriberCSV."""
    if not request.headers.get("HX-Request") == "true":
        return HttpResponseBadRequest("Bad Request - HTMX only")

    try:
        subscriber_csv = SubscriberCSV.objects.get(save_token=save_token)
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned):
        messages.error(request, "Could not find that CSV upload. Please refresh and try again.")
        return render(request, "fragments/messages.html")

    if subscriber_csv.task_state == SubscriberCSV.TASK_STATE_SUCCESS:
        messages.success(request, subscriber_csv.task_note or "Subscriber import complete.")
    elif subscriber_csv.task_state == SubscriberCSV.TASK_STATE_ERROR:
        messages.error(request, subscriber_csv.task_note or "Subscriber import failed.")

    return render(request, "backend/process_csv_status.html", {"instance": subscriber_csv})


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
def subscriber_list(request):
    query = (request.GET.get("q") or "").strip()
    subscribed_filter = (request.GET.get("subscribed") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    view_mode = request.GET.get("view", "users")  # "users" or "subscribers"

    User = get_user_model()

    if view_mode == "subscribers":
        # Legacy subscriber-only view
        subscribers = Subscriber.objects.select_related("from_csv", "user").order_by("-modified", "-pk")
        if query:
            subscribers = subscribers.filter(Q(email__icontains=query) | Q(from_csv__name__icontains=query))
        if subscribed_filter in {"true", "false"}:
            subscribers = subscribers.filter(subscribed=(subscribed_filter == "true"))
        context = {
            "subscribers": subscribers[:300],
            "subscriber_filters": {
                "q": query,
                "subscribed": subscribed_filter,
                "view": view_mode,
                "role": role_filter,
            },
            "subscriber_total": subscribers.count(),
            "view_mode": view_mode,
        }
    else:
        # Users view: show User accounts with linked Subscriber data
        users = (
            User.objects.select_related("subscriber")
            .prefetch_related("user_permissions")
            .order_by("-last_login", "-date_joined")
        )
        if query:
            users = users.filter(Q(email__icontains=query) | Q(name__icontains=query))
        if subscribed_filter == "true":
            users = users.filter(subscriber__subscribed=True)
        elif subscribed_filter == "false":
            users = users.filter(Q(subscriber__isnull=True) | Q(subscriber__subscribed=False))
        if role_filter == "staff":
            users = users.filter(is_staff=True)
        elif role_filter == "locked":
            users = users.filter(is_active=False)

        user_list = list(users[:300])
        # Pre-compute permission flags from prefetched permissions
        for u in user_list:
            perms = {p.codename for p in u.user_permissions.all()}
            u.has_perm_chief_editor = "chief_editor" in perms

        context = {
            "user_list": user_list,
            "subscriber_filters": {
                "q": query,
                "subscribed": subscribed_filter,
                "view": view_mode,
                "role": role_filter,
            },
            "user_total": users.count(),
            "view_mode": view_mode,
        }

    if request.headers.get("HX-Request") == "true":
        return render(request, "backend/_subscriber_list_results.html", context)

    return render(request, "backend/subscriber_list.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def user_toggle_chief_editor(request, user_id):
    """Promote/demote a user to/from chief editor with full permission bundle."""
    from django.contrib.auth.models import Permission as DjangoPerm

    User = get_user_model()
    target_user = get_object_or_404(User, pk=user_id)
    if target_user == request.user:
        messages.error(request, "You cannot modify your own chief editor status.")
        return redirect(reverse("backend:subscriber_list") + "?view=users")

    is_chief = target_user.has_perm("submissions.chief_editor")

    if is_chief:
        # Demote: remove only chief_editor
        perm = DjangoPerm.objects.get(content_type__app_label="submissions", codename="chief_editor")
        target_user.user_permissions.remove(perm)
        messages.success(request, f"Removed chief editor role from {target_user.email}")
    else:
        # Promote: grant full bundle
        perm_specs = [
            ("submissions", "chief_editor"),
            ("submissions", "manage_issue_builder"),
            ("submissions", "regional_coordinator"),
            ("backend", "manage_subscriber_csv"),
            ("backend", "send_newsletters"),
            ("backend", "view_newsletter_stats"),
            ("backend", "view_site_analytics"),
        ]
        for app_label, codename in perm_specs:
            try:
                perm = DjangoPerm.objects.get(content_type__app_label=app_label, codename=codename)
                target_user.user_permissions.add(perm)
            except DjangoPerm.DoesNotExist:
                pass
        if not target_user.is_staff:
            target_user.is_staff = True
            target_user.save(update_fields=["is_staff"])
        messages.success(request, f"Promoted {target_user.email} to chief editor")

    return redirect(reverse("backend:subscriber_list") + "?view=users")


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)
@require_POST
def user_toggle_active(request, user_id):
    """Toggle is_active (lock/unlock) for a user."""
    User = get_user_model()
    target_user = get_object_or_404(User, pk=user_id)
    if target_user == request.user:
        messages.error(request, "You cannot lock your own account.")
        return redirect(reverse("backend:subscriber_list") + "?view=users")
    target_user.is_active = not target_user.is_active
    target_user.save(update_fields=["is_active"])
    status = "unlocked" if target_user.is_active else "locked"
    messages.success(request, f"{target_user.email} has been {status}.")
    return redirect(reverse("backend:subscriber_list") + "?view=users")
