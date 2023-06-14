from html.parser import HTMLParser


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
