from io import BytesIO
from sys import getsizeof

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

from config.celery_app import app as celery_app

from .functions import resize_to_max_dimension


@celery_app.task(bind=True, max_retries=3, default_retry_delay=20)
def celery_resize_image(self, path, size=800):
    try:
        # File may be local or remote (S3)
        with default_storage.open(path, mode="rb") as file:
            file.seek(0)
            img = Image.open(file)

            # Remove transparency channel if present
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

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
    except Exception as e:
        # Retry the task after a delay if it fails
        raise self.retry(exc=e, max_retries=self.max_retries, countdown=self.default_retry_delay)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=20)
def celery_resize_greyscale_contrast_image(self, path, size=600):
    try:
        # File may be local or remote (S3)
        with default_storage.open(path, mode="rb") as file:
            file.seek(0)
            img = Image.open(file)

            # Remove transparency channel if present
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            width, height = img.size

            if max(width, height) > size:
                # Resize the image
                new_width, new_height = resize_to_max_dimension(width, height, size)
                img = img.resize((new_width, new_height))

            # Convert the file to greyscale
            img = img.convert("L")

            # Correct contrast
            img = ImageOps.autocontrast(img, cutoff=5)

            # Create the new file
            output = BytesIO()
            img.save(output, format="JPEG", quality=90, resampling=Image.Resampling.LANCZOS)
            output.seek(0)
            output = ContentFile(output.getvalue())
            imagefile = InMemoryUploadedFile(output, None, path, "image/jpeg", getsizeof(output), None)

            # Replace the S3 file
            default_storage.delete(path)
            default_storage.save(path, imagefile)

    except Exception as e:
        # Retry the task after a delay if it fails
        raise self.retry(exc=e, max_retries=self.max_retries, countdown=self.default_retry_delay)
