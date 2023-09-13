import csv
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import MultipleObjectsReturned
from django.http import HttpResponseBadRequest
from django.shortcuts import render

from .forms import HeaderForm, SubscriberCSVForm
from .models import SubscriberCSV


def preview_csv(file, rows=5):
    """
    Takes a CSV file and parses it using csv Sniffer and reader
    Returns a dictionary with a preview and row-count
    """
    with file.open() as file:
        decoded_file = file.read(1024).decode("UTF-8")

        # Get file properties
        dialect = csv.Sniffer().sniff(decoded_file, [",", ";"])
        has_header = csv.Sniffer().has_header(decoded_file)

        io_string = io.StringIO(decoded_file)
        preview = csv.reader(io_string, dialect=dialect)

        return {"preview": preview, "has_header": has_header}


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

            # Retain backward compatibility
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
    except (SubscriberCSV.DoesNotExist, MultipleObjectsReturned) as error:
        messages.error(request, "There was a problem updating this CSV. Please refresh the page and try again")
        raise error

    if request.method == "POST":
        form = HeaderForm(request.POST)

        if form.is_valid():
            header = form.cleaned_data["header"]
            subscriber_csv.header = header
            subscriber_csv.save()

    else:
        form = HeaderForm(initial={"header": subscriber_csv.header})

    context = {"header_form": form}

    return render(request, "backend/edit_csv_header", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def process_csv(request):
    pass


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def dashboard(request):
    pass
