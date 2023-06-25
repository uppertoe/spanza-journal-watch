import re

from django import template

register = template.Library()


@register.filter
def wrapchars(value):
    words = re.split(r"(\s+|\n)", value)  # Split using regex pattern for spaces and line breaks

    for i, word in enumerate(words):
        if len(word) > 50:
            words[i] = re.sub(r"(.{45})", r"\1<br>", word)  # Insert <br> after every 50 characters

    return "".join(words)
