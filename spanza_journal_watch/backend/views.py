import csv
import io

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render

from .forms import SubscriberCSVForm


def preview_csv(file, rows=5):
    """
    Takes a CSV file and parses it using csv Sniffer and reader
    Returns a dictionary with a preview and row-count
    """
    decoded_file = file.read().decode("UTF-8")
    io_string = io.StringIO(decoded_file)
    dialect = csv.Sniffer().sniff(io_string)
    preview = csv.reader(io_string, dialect)
    row_count = sum(1 for _ in preview)  # Generator to count number of rows
    return {"preview": preview[:rows], "row-count": row_count}


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def upload_subscriber_csv(request):
    if request.method == "POST":
        form = SubscriberCSVForm(request.POST, request.FILES)
        context = {"form": form}

        if form.is_valid():
            instance = form.save()
            context["id"] = instance.id
            context.update(preview_csv(request.FILES["file"]))  # Merge with the preview_csv dictionary

            # Retain backward compatibility
            if request.headers.get("HX-Request") == "true":
                template = "backend/preview_csv_htmx.html"
            else:
                template = "backend/preview_csv.html"

            return render(request, template, context)

    else:
        form = SubscriberCSVForm()

    return render(request, "backend/upload_subscriber_csv.html", context)


@login_required
@permission_required("backend.manage_subscriber_csv", raise_exception=True)  # Prevents login loop
def process_csv(request):
    pass
