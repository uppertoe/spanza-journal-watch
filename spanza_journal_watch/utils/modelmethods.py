import datetime
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import ImageField
from django.utils.text import slugify
from PIL import Image

from config.celery_app import app as celery_app
from spanza_journal_watch.utils.functions import process_model_instance


def resize_to_max_dimension(width, height, target):
    # Determine the larger dimension
    max_dimension = max(width, height)

    # Calculate the ratio between the dimensions
    ratio = max_dimension / target

    # Calculate the new width and height while maintaining the aspect ratio
    new_width = int(width / ratio)
    new_height = int(height / ratio)

    return new_width, new_height


def name_image(instance, timestamp=False):
    name = f"{slugify(str(instance))}-image"
    if timestamp:
        current_datetime = datetime.datetime.now().strftime("%d%m%Y%H%M%S")
        name = name + current_datetime
    return name + ".jpg"


def get_instance_image_field(instance):
    # Returns the first matching ImageField
    for field in instance._meta.get_fields():
        if isinstance(field, ImageField):
            return field.name
    return None


@celery_app.task
def resize_image(app_label, model_name, pk, size=600):
    # Get instance via Model and pk to avoid stale references
    instance = process_model_instance(app_label, model_name, pk)
    image_field_name = get_instance_image_field(instance)
    if not image_field_name:
        return print(f"Warning: no compatible ImageField for {instance}")

    print(f"Celery 1 {instance.feature_image.path}")

    image = getattr(instance, image_field_name)
    print(f"Celery: {image.path}")

    image_file = default_storage.open(image.path)
    with Image.open(image_file) as img:  # Ensure file is closed
        width, height = img.size

        if max(width, height) > size:
            # Resize the image
            new_width, new_height = resize_to_max_dimension(width, height, size)
            resized_img = img.resize((new_width, new_height))

            # Operate in memory
            output = BytesIO()
            resized_img.save(output, format="JPEG", quality=90, resampling=Image.Resampling.LANCZOS)
            output.seek(0)
            resized_image = ContentFile(output.getvalue())

            # Save the resized image to the specific field
            setattr(instance, image_field_name, resized_image)
            instance.save()
    image_file.close()
