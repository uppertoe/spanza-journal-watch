import csv
import io
from pathlib import Path

from django import forms
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from config.celery_app import app as celery_app


class MultipleEmailColumnsException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class NoEmailColumnsFoundException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


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
EMAIL_HEADER_CANDIDATES = {"email", "email address", "e-mail", "e-mail address", "mail"}


def _normalize_cell(value):
    if value is None:
        return ""
    return str(value).strip()


def _iter_csv_rows(file_obj):
    decoded_file = file_obj.read().decode("UTF-8-SIG")
    try:
        dialect = csv.Sniffer().sniff(decoded_file, DELIMITERS)
    except csv.Error:
        for delimiter in DELIMITERS:
            if delimiter in decoded_file:
                raise ValidationError("Not a valid CSV file")
        dialect = csv.excel

    io_string = io.StringIO(decoded_file)
    for row in csv.reader(io_string, dialect=dialect):
        yield [_normalize_cell(value) for value in row]


def _iter_xlsx_rows(file_obj):
    try:
        from openpyxl import load_workbook
    except Exception as error:
        raise ValidationError(f"XLSX support requires openpyxl: {error}")

    workbook = load_workbook(filename=file_obj, read_only=True, data_only=True)
    worksheet = workbook.active
    for row in worksheet.iter_rows(values_only=True):
        yield [_normalize_cell(value) for value in row]


def _load_rows(subscriber_csv):
    suffix = Path(subscriber_csv.file.name).suffix.lower()
    with subscriber_csv.file.open("rb") as file_obj:
        if suffix == ".xlsx":
            return [row for row in _iter_xlsx_rows(file_obj) if any(value for value in row)]
        return [row for row in _iter_csv_rows(file_obj) if any(value for value in row)]


def _find_header_email_column_index(headers):
    matches = []
    for idx, raw_header in enumerate(headers):
        normalized = (raw_header or "").strip().lower()
        if normalized in EMAIL_HEADER_CANDIDATES:
            matches.append(idx)

    if len(matches) > 1:
        raise MultipleEmailColumnsException("Multiple possible email header columns detected.")
    return matches[0] if matches else None


def _find_data_email_column_index(rows):
    if not rows:
        return None

    sample_rows = rows[: min(50, len(rows))]
    max_cols = max(len(row) for row in sample_rows)
    valid_counts = [0] * max_cols

    for row in sample_rows:
        for idx in range(max_cols):
            value = row[idx].strip().lower() if idx < len(row) else ""
            if not value:
                continue
            try:
                validate_email(value)
                valid_counts[idx] += 1
            except ValidationError:
                continue

    best_score = max(valid_counts) if valid_counts else 0
    if best_score == 0:
        return None

    winners = [idx for idx, score in enumerate(valid_counts) if score == best_score]
    if len(winners) > 1:
        raise MultipleEmailColumnsException("Multiple columns contain valid emails.")

    return winners[0]


def process_subscriber_csv_record(subscriber_csv):
    from spanza_journal_watch.newsletter.models import Subscriber

    rows = _load_rows(subscriber_csv)
    if not rows:
        raise NoEmailColumnsFoundException("No rows found in uploaded file")

    has_header = bool(subscriber_csv.header)
    headers = rows[0] if has_header else []
    data_rows = rows[1:] if has_header else rows

    email_col_index = _find_header_email_column_index(headers) if has_header else None
    if email_col_index is None:
        email_col_index = _find_data_email_column_index(data_rows)

    if email_col_index is None:
        raise NoEmailColumnsFoundException("Could not detect an email column")

    rows_parsed = 0
    records_added = 0
    invalid_email_count = 0
    duplicate_in_file_count = 0
    already_subscribed_count = 0
    seen_in_file = set()

    for row in data_rows:
        rows_parsed += 1
        email = row[email_col_index].strip().lower() if email_col_index < len(row) else ""
        if not email:
            invalid_email_count += 1
            continue

        try:
            validate_email(email)
        except ValidationError:
            invalid_email_count += 1
            continue

        if email in seen_in_file:
            duplicate_in_file_count += 1
            continue
        seen_in_file.add(email)

        if Subscriber.objects.filter(email=email).exists():
            already_subscribed_count += 1
            continue

        Subscriber.objects.create(email=email, subscribed=True, from_csv=subscriber_csv)
        records_added += 1

    subscriber_csv.processed = True
    subscriber_csv.row_count = rows_parsed
    subscriber_csv.email_added_count = records_added
    subscriber_csv.save(update_fields=["processed", "row_count", "email_added_count", "modified"])

    return {
        "rows_parsed": rows_parsed,
        "records_added": records_added,
        "records_skipped": invalid_email_count + duplicate_in_file_count + already_subscribed_count,
        "invalid_email_count": invalid_email_count,
        "duplicate_in_file_count": duplicate_in_file_count,
        "already_subscribed_count": already_subscribed_count,
        "email_column": headers[email_col_index]
        if has_header and email_col_index < len(headers)
        else f"Column {email_col_index + 1}",
    }


@celery_app.task()
def process_subscriber_csv(subscriber_csv_pk):
    # Prevent circular import
    from .models import SubscriberCSV

    subscriber_csv = SubscriberCSV.objects.get(pk=subscriber_csv_pk)
    return process_subscriber_csv_record(subscriber_csv)
