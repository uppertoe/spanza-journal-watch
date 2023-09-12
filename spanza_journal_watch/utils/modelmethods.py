import os

from django.core.files.storage import default_storage
from django.utils.text import slugify


def _name_object(instance, filename, appended_str, folder="uploads"):
    model_name = type(instance).__name__.lower()
    upload_to = f"{folder}/{model_name}"
    ext = filename.split(".")[-1]
    name = f"{slugify(str(instance))}-{appended_str}"

    # Assemble new filename
    filename = ".".join([name, ext])

    # Prevents celery working on the old file
    if default_storage.exists(filename):
        default_storage.delete(filename)

    return os.path.join(upload_to, filename)


def name_image(instance, filename):
    return _name_object(instance, filename, "image")


def name_font(instance, filename):
    return _name_object(instance, filename, "font")


def name_csv(instance, filename):
    return _name_object(instance, filename, "list", "backend")
