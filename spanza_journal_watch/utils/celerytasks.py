from io import BytesIO
from sys import getsizeof

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image

from config.celery_app import app as celery_app

from .functions import resize_to_max_dimension


@celery_app.task
def celery_resize_image(path, size=600):
    # File may be local or remote (S3)
    with default_storage.open(path, mode="rb") as file:
        file.seek(0)
        img = Image.open(file)

        width, height = img.size

        if max(width, height) > size:
            # Resize the image
            new_width, new_height = resize_to_max_dimension(width, height, size)
            resized_img = img.resize((new_width, new_height))

            # Create the new file
            output = BytesIO()
            resized_img.save(output, format="JPEG", quality=90, resampling=Image.Resampling.LANCZOS)
            output.seek(0)
            output = ContentFile(output.getvalue())
            imagefile = InMemoryUploadedFile(output, None, path, "image/jpeg", getsizeof(output), None)

            # Replace the S3 file
            default_storage.delete(path)
            default_storage.save(path, imagefile)
