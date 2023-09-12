from django.db import models

from spanza_journal_watch.utils.modelmethods import name_csv


class SubscriberCSV(models.Model):
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=name_csv)
    confirmed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    processed = models.BooleanField(default=False)
    modified = models.DateTimeField(auto_now=True)
    email_count = models.PositiveIntegerField()
    email_added_count = models.PositiveIntegerField()

    class Meta:
        permissions = [("manage_subscriber_csv", "Can create and edit CSV subscriber lists")]

    def __str__(self):
        return self.name
