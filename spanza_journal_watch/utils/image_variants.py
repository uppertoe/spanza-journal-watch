from pathlib import Path


def image_variant_name(path, width, extension="webp"):
    source = Path(path)
    return str(source.with_name(f"{source.stem}__w{int(width)}.{extension}"))

