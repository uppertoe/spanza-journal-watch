from django import forms

from .models import Subscriber


class SubscriberForm(forms.ModelForm):
    class Meta:
        model = Subscriber
        fields = [
            "email",
        ]
        widgets = {
            "email": forms.TextInput(attrs={"class": "form-control", "autofocus": "", "placeholder": "Email address"})
        }
        error_messages = {"email": {"invalid": "Please enter a valid email address."}}

    def clean_email(self):
        return self.cleaned_data["email"].lower()
