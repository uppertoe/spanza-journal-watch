from io import BytesIO

from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image

from config.celery_app import app as celery_app

from .functions import process_model_instance
from .modelmethods import get_instance_image_field, resize_to_max_dimension


@celery_app.task
def celery_resize_image(app_label, model_name, pk, size=600):
    # Get instance via Model and pk to avoid stale references
    instance = process_model_instance(app_label, model_name, pk)
    image_field_name = get_instance_image_field(instance)
    if not image_field_name:
        return print(f"Warning: no compatible ImageField for {instance}")
    image = getattr(instance, image_field_name)

    with default_storage.open(image.name, mode="rb") as file:
        # Create memory object
        buffer = BytesIO()
        for chunk in file.chunks():
            buffer.write(chunk)
        buffer.seek(0)

        img = Image.open(buffer)

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
            imagefile = InMemoryUploadedFile(output, None, image.name, "image/jpeg", None, None)

            # file.delete(save=False)
            path = default_storage.save(image.name, imagefile)

            model = django_apps.get_model(app_label, model_name)
            fields = {f"{image_field_name}": path}
            model.objects.filter(pk=pk).update(**fields)
