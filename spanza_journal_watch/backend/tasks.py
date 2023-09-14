import csv
import io

from django import forms
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.template.loader import render_to_string

from config.celery_app import app as celery_app


class MultipleEmailColumnsException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class NoEmailColumnsFoundException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


@celery_app.task()
def send_csv_processing_results(errors, records_added):
    staff = get_user_model().objects.filter(is_staff=True)
    context = {
        "errors": errors,
        "records_added": records_added,
    }
    template = "backend/csv_processing_results_email.txt"

    subject = "CSV processing results"
    body = render_to_string(template, context)

    for member in staff:
        send_mail(
            subject,
            body,
            None,
            [member.email],
        )


def get_celery_subscriber_form():
    # Wrapped in fuction to avoid circular import

    Subscriber = apps.get_model("newsletter", "Subscriber")

    class CelerySubscriberForm(forms.ModelForm):
        class Meta:
            model = Subscriber
            fields = [
                "email",
                "subscribed",
                "from_csv",
            ]

        def clean_email(self):
            email = self.cleaned_data["email"].lower()
            if Subscriber.objects.filter(email=email).exists():
                raise forms.ValidationError(f"Email {email} is already a subscriber")
            return email

    return CelerySubscriberForm


DELIMITERS = [",", ";", "\t", " "]


@celery_app.task()
def process_subscriber_csv(subscriber_csv_pk):
    # Prevent circular import
    from .models import SubscriberCSV

    # Get the SubscriberCSV; allow errors to propagate
    subscriber_csv = SubscriberCSV.objects.get(pk=subscriber_csv_pk)

    # Load the CSV
    with subscriber_csv.file.open() as file:
        decoded_file = file.read().decode("UTF-8-SIG")

        try:
            dialect = csv.Sniffer().sniff(decoded_file, DELIMITERS)
        except csv.Error:
            for delimiter in DELIMITERS:
                if delimiter in decoded_file:
                    raise ValidationError("Not a valid CSV file")
            # No delimiter found; likely single-columm file
            dialect = csv.excel

        # Use user-supplied entry
        has_header = subscriber_csv.header

        io_string = io.StringIO(decoded_file)
        document = csv.reader(io_string, dialect=dialect)

        email_col_index = None
        start = 1 if has_header else 0
        errors = []
        rows_parsed = 0
        records_added = 0

        for index, row in enumerate(document):
            # Skip the header
            if index < start:
                continue

            # Set the email column on the first run
            if email_col_index is None:
                for index, value in enumerate(row):
                    try:
                        validate_email(value)
                        if email_col_index is not None:
                            raise MultipleEmailColumnsException(f"More than one email column in CSV {subscriber_csv}")
                        email_col_index = index
                    except ValidationError:
                        continue

            if email_col_index is None:
                raise NoEmailColumnsFoundException("No email addresses in first row")

            # Get the email address from its column
            email = row[email_col_index]

            # Assemble the dictionary for the form
            subscriber = {
                "email": email,
                "subscribed": True,
                "from_csv": subscriber_csv,
            }

            # Process the form
            CelerySubscriberForm = get_celery_subscriber_form()
            form = CelerySubscriberForm(subscriber)
            if form.is_valid():
                form.save()
                records_added += 1
            else:
                errors.append(form.errors)

            # Record row
            rows_parsed += 1

    # Mark as processed
    subscriber_csv.processed = True
    subscriber_csv.row_count = rows_parsed
    subscriber_csv.email_added_count = records_added
    subscriber_csv.save()

    # Email the results to staff
    send_csv_processing_results.delay(errors, records_added)
