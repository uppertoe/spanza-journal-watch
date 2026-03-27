from io import BytesIO
from pathlib import Path

from django.apps import apps
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

from config.celery_app import app as celery_app

from .functions import resize_to_max_dimension
from .image_variants import image_variant_name


def _webp_name(path):
    return str(Path(path).with_suffix(".webp"))


def _original_format_name(path):
    return str(Path(path))


def _build_output_image(img, path, target_format):
    output = BytesIO()

    if target_format == "webp":
        img.save(
            output,
            format="WEBP",
            quality=82,
            method=6,
        )
        next_path = _webp_name(path)
        content_type = "image/webp"
    else:
        suffix = Path(path).suffix.lower()
        next_path = _original_format_name(path)

        if suffix in {".jpg", ".jpeg"}:
            img = img.convert("RGB")
            img.save(
                output,
                format="JPEG",
                quality=86,
                optimize=True,
                progressive=True,
            )
            content_type = "image/jpeg"
        elif suffix == ".png":
            img.save(output, format="PNG", optimize=True)
            content_type = "image/png"
        else:
            img.save(output, format="PNG", optimize=True)
            next_path = str(Path(path).with_suffix(".png"))
            content_type = "image/png"

    size = output.tell()
    output.seek(0)
    return output, next_path, content_type, size


def _delete_variant_files(path, variant_widths):
    for width in variant_widths:
        variant_path = image_variant_name(path, width)
        if default_storage.exists(variant_path):
            default_storage.delete(variant_path)


def _save_variant_images(img, path, variant_widths):
    for width in variant_widths:
        if img.width <= width:
            continue

        variant = img.copy()
        new_height = int(round(img.height * (width / img.width)))
        variant = variant.resize((width, new_height), Image.Resampling.LANCZOS)

        output = BytesIO()
        variant.save(
            output,
            format="WEBP",
            quality=76,
            method=6,
        )
        size = output.tell()
        output.seek(0)

        variant_path = image_variant_name(path, width)
        if default_storage.exists(variant_path):
            default_storage.delete(variant_path)

        imagefile = InMemoryUploadedFile(output, None, variant_path, "image/webp", size, None)
        default_storage.save(variant_path, imagefile)


def resize_uploaded_image(model_label, instance_pk, field_name, size=800, target_format="webp", variant_widths=()):
    model = apps.get_model(model_label)
    instance = model.objects.filter(pk=instance_pk).only(field_name).first()
    if not instance:
        return

    image_field = getattr(instance, field_name)
    if not image_field:
        return

    path = image_field.name

    # File may be local or remote (S3)
    with default_storage.open(path, mode="rb") as file:
        file.seek(0)
        img = ImageOps.exif_transpose(Image.open(file))

        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        target_mode = "RGBA" if has_alpha else "RGB"
        if img.mode != target_mode:
            img = img.convert(target_mode)

        width, height = img.size

        if max(width, height) > size:
            new_width, new_height = resize_to_max_dimension(width, height, size)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        output, next_path, content_type, output_size = _build_output_image(img, path, target_format)
        imagefile = InMemoryUploadedFile(output, None, next_path, content_type, output_size, None)

        if next_path == path:
            if default_storage.exists(path):
                default_storage.delete(path)
            saved_path = default_storage.save(path, imagefile)
            if saved_path != path:
                model.objects.filter(pk=instance_pk).update(**{field_name: saved_path})
                if variant_widths:
                    _delete_variant_files(path, variant_widths)
                    _save_variant_images(img, saved_path, variant_widths)
            elif variant_widths:
                _save_variant_images(img, path, variant_widths)
            return

        if variant_widths:
            _delete_variant_files(path, variant_widths)

        if default_storage.exists(next_path):
            default_storage.delete(next_path)

        saved_path = default_storage.save(next_path, imagefile)
        model.objects.filter(pk=instance_pk).update(**{field_name: saved_path})

        if variant_widths:
            _save_variant_images(img, saved_path, variant_widths)

        if default_storage.exists(path):
            default_storage.delete(path)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=20)
def celery_resize_image(self, model_label, instance_pk, field_name, size=800, target_format="webp", variant_widths=()):
    try:
        resize_uploaded_image(
            model_label,
            instance_pk,
            field_name,
            size=size,
            target_format=target_format,
            variant_widths=variant_widths,
        )
    except Exception as e:
        # Retry the task after a delay if it fails
        raise self.retry(exc=e, max_retries=self.max_retries, countdown=self.default_retry_delay)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=20)
def celery_resize_greyscale_contrast_image(self, path, size=600, aspect_ratio=21 / 9):
    try:
        # File may be local or remote (S3)
        with default_storage.open(path, mode="rb") as file:
            file.seek(0)
            img = Image.open(file)

            # Remove transparency channel if present
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            width, height = img.size

            # Calculate the dimensions of the center rectangle with the desired aspect ratio
            target_width = int(min(width, height * aspect_ratio))
            target_height = int(min(height, width / aspect_ratio))

            # Calculate the position to start cropping
            left = (width - target_width) / 2
            top = (height - target_height) / 2
            right = (width + target_width) / 2
            bottom = (height + target_height) / 2

            # Crop the image using the calculated dimensions
            img = img.crop((left, top, right, bottom))

            if max(target_width, target_height) > size:
                # Resize the image
                new_width, new_height = resize_to_max_dimension(target_width, target_height, size)
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
