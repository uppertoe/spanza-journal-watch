from django.template.loader import render_to_string


class HtmxMessagesMiddleware:
    """
    Append unconsumed Django messages to HTMX partial responses as an OOB swap.

    When a view adds messages (messages.success, messages.error, …) and returns
    a partial HTML fragment (no full-page render), the messages template is never
    rendered and Django keeps them in the session.  They then appear on the next
    full-page load — often the public frontend.

    This middleware detects HTMX requests where messages were not consumed by the
    response template and appends the messages fragment as an out-of-band swap so
    HTMX inserts them into #oob-message immediately.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not (
            request.headers.get("HX-Request") == "true"
            and response.status_code == 200
            and "text/html" in response.get("Content-Type", "")
        ):
            return response

        storage = getattr(request, "_messages", None)
        if storage is None or storage.used:
            # Messages were already consumed by the template.
            return response

        messages_list = list(storage)  # iterates storage, marks it used
        if not messages_list:
            return response

        fragment = render_to_string(
            "fragments/messages.html",
            {"messages": messages_list},
            request=request,
        )
        response.content += fragment.encode()
        return response
