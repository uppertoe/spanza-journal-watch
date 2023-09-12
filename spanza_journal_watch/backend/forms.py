from django import forms

from .models import SubscriberCSV


class SubscriberCSVForm(forms.ModelForm):
    class Meta:
        model = SubscriberCSV
        fields = [
            "name",
            "file",
        ]
