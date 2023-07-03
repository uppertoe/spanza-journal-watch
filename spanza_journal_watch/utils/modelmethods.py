import datetime
from io import BytesIO

# from django.apps import apps as django_apps
# from django.core.files import File
# from django.core.files.base import ContentFile
from django.core.files.uploadedfile import TemporaryUploadedFile

# from django.core.files.storage import default_storage
from django.db.models import ImageField
from django.utils.text import slugify
from PIL import Image

# from config.celery_app import app as celery_app
# from spanza_journal_watch.utils.functions import process_model_instance


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


def resize_image(image, size=600):
    img = Image.open(image)
    width, height = img.size

    if max(width, height) > size:
        new_width, new_height = resize_to_max_dimension(width, height, size)
        resized_img = img.resize((new_width, new_height))

        # Operate in memory
        output = BytesIO()
        resized_img.save(output, format="JPEG", quality=95, resampling=Image.Resampling.LANCZOS)
        output.seek(0)

        # Create a TemporaryUploadedFile object
        temp_file = TemporaryUploadedFile(name=image.name)
        temp_file.write(output.getvalue())
        temp_file.seek(0)

        return temp_file
    return image
