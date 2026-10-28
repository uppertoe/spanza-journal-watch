from spanza_journal_watch.utils.sentry import before_breadcrumb, before_send, redact_emails


def test_redact_emails_replaces_addresses_in_nested_values():
    value = {"to": ["Jane.Citizen+jw@example.org.au"], "note": ("sent to bob@example.com",), "count": 2}
    assert redact_emails(value) == {"to": ["[email]"], "note": ("sent to [email]",), "count": 2}


def test_before_send_redacts_message_logentry_exception_and_breadcrumbs():
    event = {
        "message": "Bounce from jane@example.org",
        "logentry": {"message": "Failed to send to %s", "params": ["jane@example.org"]},
        "exception": {"values": [{"type": "ValueError", "value": "jane@example.org is invalid"}]},
        "breadcrumbs": {"values": [{"message": "user jane@example.org", "data": {"email": "jane@example.org"}}]},
    }

    redacted = before_send(event, None)

    assert redacted["message"] == "Bounce from [email]"
    assert redacted["logentry"]["params"] == ["[email]"]
    assert redacted["exception"]["values"][0]["value"] == "[email] is invalid"
    assert redacted["breadcrumbs"]["values"][0] == {"message": "user [email]", "data": {"email": "[email]"}}


def test_before_breadcrumb_leaves_text_without_addresses_alone():
    crumb = {"message": "GET /reviews/caudal-blocks", "data": {"status": 200}}
    assert before_breadcrumb(dict(crumb), None) == crumb
