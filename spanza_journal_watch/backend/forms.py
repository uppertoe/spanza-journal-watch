import csv
import io

from django import forms
from django.core.exceptions import ValidationError

from .models import SubscriberCSV


def csv_size(file):
    limit = 1 * 1024 * 1024
    if file.size > limit:
        raise ValidationError({"file": "File too large. Size should not exceed 1 megabyte."})


def peek_csv(file, user_header=None):
    try:
        decoded_file = file.read(1024).decode("UTF-8")
        dialect = csv.Sniffer().sniff(decoded_file, [",", ";"])
        has_header = csv.Sniffer().has_header(decoded_file)
    except (csv.Error, UnicodeDecodeError) as error:
        print(f"Error handling uploaded CSV: {error}")
        raise ValidationError({"file": "Not a valid CSV file"})

    # Determine column number and names
    delimiter = str(dialect.delimiter)
    fieldnames = decoded_file.split("\n")[0].split(delimiter)

    # If user has selected header
    if user_header is not None:
        has_header = user_header

    if not has_header:
        column_count = len(fieldnames)
        fieldnames = []
        for i in range(column_count):
            fieldnames.append(f"Column {i+1}")
    else:
        fieldnames = None  # Allow DictReader to use the first row as fieldnames

    io_string = io.StringIO(decoded_file)
    preview = csv.DictReader(io_string, fieldnames=fieldnames, dialect=dialect)

    return {"preview": preview, "has_header": has_header}


class SubscriberCSVForm(forms.ModelForm):
    class Meta:
        model = SubscriberCSV
        fields = [
            "name",
            "file",
        ]

    def clean(self):
        cleaned_data = super().clean()

        # File is already opened by Django
        file = cleaned_data["file"]

        # Validate and preview the CSV
        csv_size(file)
        csv_preview = peek_csv(file)

        cleaned_data.update(csv_preview)
        return cleaned_data


class HeaderForm(forms.Form):
    header = forms.BooleanField(label="The first row of this CSV is a column heading", required=False)
