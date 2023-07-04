from html.parser import HTMLParser

from django.apps import apps as django_apps
from django.utils.html import strip_tags


def resize_to_max_dimension(width, height, target):
    # Determine the larger dimension
    max_dimension = max(width, height)

    # Calculate the ratio between the dimensions
    ratio = max_dimension / target

    # Calculate the new width and height while maintaining the aspect ratio
    new_width = int(width / ratio)
    new_height = int(height / ratio)

    return new_width, new_height


def process_model_instance(app_label, model_name, instance_pk):
    model = django_apps.get_model(app_label=app_label, model_name=model_name)
    instance = model.objects.get(pk=instance_pk)
    return instance


def get_unique_slug(instance, slug):
    model = instance.__class__
    max_length = model._meta.get_field("slug").max_length

    if len(slug) > max_length:
        # Find the last space before the maximum length
        last_space_index = slug.rfind("-", 0, max_length)
        if last_space_index != -1:
            slug = slug[:last_space_index]

    unique_slug = slug
    counter = 1

    while model.objects.filter(slug=unique_slug).exists():
        # Leave space for counter
        truncated_slug = unique_slug[: max_length - 2]
        unique_slug = f"{truncated_slug}-{counter}"
        counter += 1

        # Further reduce the slug length if the counter impinges on max_length
        if len(unique_slug) > max_length:
            truncated_slug = slug[: max_length - len(str(counter)) - 1]
            unique_slug = f"{truncated_slug}-{counter}"

    return unique_slug


def shorten_text(text, char_limit):
    if len(text) <= char_limit:
        return text

    # Find the last space within the character limit
    last_space_index = text[:char_limit].rfind(" ")

    if last_space_index != -1:
        # Return the text up to the last space
        return text[:last_space_index] + "..."
    else:
        # If no space is found, cut the text at the character limit
        return text[:char_limit] + "..."


def estimate_reading_time(html_text, words_per_minute=200):
    # Strip HTML tags and extract plain text
    plain_text = strip_tags(html_text)
    words = plain_text.split()
    word_count = len(words)

    minutes = word_count / words_per_minute
    minutes = max(1, minutes)  # Ensure a minimum reading time of 1 minute
    minutes = round(minutes)

    return minutes


class HTMLShortener(HTMLParser):
    def __init__(self, char_limit):
        super().__init__()
        self.char_limit = char_limit
        self.output = []
        self.char_count = 0
        self.truncated = False
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        if self.char_count >= self.char_limit:
            return

        self.tag_stack.append(tag)
        self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.char_count >= self.char_limit:
            return

        if tag in self.tag_stack:
            while self.tag_stack and self.tag_stack[-1] != tag:
                self.output.append(f"</{self.tag_stack.pop()}>")

            if self.tag_stack and self.tag_stack[-1] == tag:
                self.output.append(f"</{self.tag_stack.pop()}>")

    def handle_data(self, data):
        if self.char_count >= self.char_limit:
            return

        remaining_chars = self.char_limit - self.char_count
        if len(data) <= remaining_chars:
            self.output.append(data)
            self.char_count += len(data)
        else:
            self.output.append(data[:remaining_chars])
            self.char_count = self.char_limit
            self.truncated = True

    def get_shortened_html(self):
        return "".join(self.output)

    def truncate_html(self, html):
        self.feed(html)
        self.close()

        if self.truncated:
            self.output.append("...")

        # Close any remaining open tags
        while self.tag_stack:
            self.output.append(f"</{self.tag_stack.pop()}>")

        return self.get_shortened_html()
