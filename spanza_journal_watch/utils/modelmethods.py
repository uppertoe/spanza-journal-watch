import os

from django.core.files.storage import default_storage
from django.utils.text import slugify


def name_image(instance, filename):
    model_name = type(instance).__name__.lower()
    upload_to = f"uploads/{model_name}"
    ext = filename.split(".")[-1]
    name = f"{slugify(str(instance))}-image"
    filename = ".".join([name, ext])

    # Prevents celery working on the old file
    if default_storage.exists(filename):
        default_storage.delete(filename)

    return os.path.join(upload_to, filename)
