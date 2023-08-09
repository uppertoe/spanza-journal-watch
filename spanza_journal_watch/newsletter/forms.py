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
