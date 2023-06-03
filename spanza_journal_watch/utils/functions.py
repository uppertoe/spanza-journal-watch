from random import randint


def unique_slugify(instance, slug):
    model = instance.__class__
    truncated_slug = slug[:253]  # Ensure max_length == 255
    unique_slug = truncated_slug
    while model.objects.filter(slug=unique_slug).exists():
        unique_slug = truncated_slug + str(randint(1, 999))
    return unique_slug
