import base64
import uuid

from django.db import models

from spanza_journal_watch.utils.modelmethods import name_csv


class SubscriberCSV(models.Model):
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=name_csv)
    confirmed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    modified = models.DateTimeField(auto_now=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    email_added_count = models.PositiveIntegerField(null=True, blank=True)
    save_token = models.CharField(max_length=64, blank=True, null=True)
    header = models.BooleanField(default=False)

    class Meta:
        permissions = [("manage_subscriber_csv", "Can create and edit CSV subscriber lists")]

    def generate_save_token(self):
        r_uuid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("utf-8")
        return r_uuid.replace("=", "")

    def is_ready_to_process(self):
        return self.confirmed and not self.processed

    def save(self, *args, **kwargs):
        # Refresh the save token
        self.save_token = self.generate_save_token()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
