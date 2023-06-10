def unique_slugify(instance, slug):
    model = instance.__class__
    max_length = model._meta.get_field("slug").max_length

    truncated_slug = slug[: max_length - 2]  # Leave space for counter
    unique_slug = truncated_slug
    counter = 1

    while model.objects.filter(slug=unique_slug).exists():
        unique_slug = f"{truncated_slug}-{counter}"
        counter += 1

        # If the generated slug exceeds the max_length, truncate it further
        if len(unique_slug) > max_length:
            truncated_slug = slug[: max_length - len(str(counter)) - 1]
            unique_slug = f"{truncated_slug}-{counter}"

    return unique_slug
