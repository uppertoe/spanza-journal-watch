import os

from django.core.files.storage import default_storage
from django.utils.text import slugify


def resize_to_max_dimension(width, height, target):
    # Determine the larger dimension
    max_dimension = max(width, height)

    # Calculate the ratio between the dimensions
    ratio = max_dimension / target

    # Calculate the new width and height while maintaining the aspect ratio
    new_width = int(width / ratio)
    new_height = int(height / ratio)

    return new_width, new_height


def name_image(instance, filename):
    upload_to = "uploads/review"
    ext = filename.split(".")[-1]
    name = f"{slugify(str(instance))}-image"
    filename = ".".join([name, ext])

    # Prevents celery working on the old file
    if default_storage.exists(filename):
        default_storage.delete(filename)

    return os.path.join(upload_to, filename)
