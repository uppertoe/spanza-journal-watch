from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import MultipleObjectsReturned
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render

from spanza_journal_watch.analytics.models import NewsletterClick, NewsletterOpen
from spanza_journal_watch.newsletter.models import Newsletter
from spanza_journal_watch.newsletter.tasks import send_newsletter

from .forms import HeaderForm, SubscriberCSVForm, peek_csv
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
def final_newsletter(request, send_token):
    # Provides last check before sending
    return render(request, "backend/final_newsletter.html", {"send_token": send_token})


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

    subscriber_opens = NewsletterOpen.objects.filter(newsletter=newsletter).values("subscriber")
    total_opens = subscriber_opens.count()
    opens = subscriber_opens.distinct().count()

    subscriber_clicks = NewsletterClick.objects.filter(newsletter=newsletter).values("subscriber")
    total_clicks = subscriber_clicks.count()
    clicks = subscriber_clicks.distinct().count()

    percentage = f"{str(round(clicks/opens*100))}%" if opens else "0"

    context = {
        "newsletter": newsletter,
        "total_opens": total_opens,
        "opens": opens,
        "total_clicks": total_clicks,
        "clicks": clicks,
        "percentage": percentage,
    }

    template = "backend/newsletter_stats_detail.html"

    return render(request, template, context)
