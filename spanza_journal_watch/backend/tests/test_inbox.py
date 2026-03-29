"""
Tests for the inbox feature.

Covers:
1.  _normalize_subject — subject normalisation helper
2.  EmailThread model — __str__, ordering, has_unread semantics
3.  SentEmail model — __str__
4.  Inbox views — auth/permission guards (302 → login, 403 for wrong role)
5.  inbox view — thread list, ordering, unread indicator
6.  inbox_thread view — timeline ordering, marks thread/messages read, 404
7.  inbox_reply view — creates SentEmail, sends email, empty-body guard,
                       failed send doesn't create SentEmail, updates timestamp
8.  Context processor — inbox_unread_count for chief_editor and others
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from spanza_journal_watch.backend.context_processors import selected_issue
from spanza_journal_watch.backend.models import BackendPreference, EmailThread, InboundEmail, SentEmail
from spanza_journal_watch.backend.signals import _normalize_subject
from spanza_journal_watch.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CHIEF_EDITOR = "submissions.chief_editor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grant(user, *perm_strings):
    for perm_str in perm_strings:
        app_label, codename = perm_str.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)


def editor_client():
    u = UserFactory()
    _grant(u, CHIEF_EDITOR)
    c = Client()
    c.force_login(u)
    return c, u


def plain_client():
    u = UserFactory()
    c = Client()
    c.force_login(u)
    return c, u


def make_thread(*, subject="Test Subject", external="sender@example.com", unread=True, offset_seconds=0):
    return EmailThread.objects.create(
        subject=subject,
        external_address=external,
        last_message_at=timezone.now() - timezone.timedelta(seconds=offset_seconds),
        has_unread=unread,
    )


def make_inbound(thread, *, body="Hello", read=False, seconds_ago=0):
    return InboundEmail.objects.create(
        thread=thread,
        sender=thread.external_address,
        body=body,
        sent_timestamp=timezone.now() - timezone.timedelta(seconds=seconds_ago),
        read=read,
    )


def make_sent(thread, user, *, body="Reply", seconds_ago=0):
    return SentEmail.objects.create(
        thread=thread,
        recipient=thread.external_address,
        subject=f"Re: {thread.subject}",
        body=body,
        sent_by=user,
        message_id=f"<{timezone.now().timestamp()}@example.com>",
    )


# ---------------------------------------------------------------------------
# 1. _normalize_subject
# ---------------------------------------------------------------------------


class TestNormalizeSubject:
    def test_strips_re_prefix(self):
        assert _normalize_subject("Re: Hello world") == "Hello world"

    def test_strips_re_case_insensitive(self):
        assert _normalize_subject("RE: Hello world") == "Hello world"

    def test_strips_fwd_prefix(self):
        assert _normalize_subject("Fwd: Hello world") == "Hello world"

    def test_strips_fw_prefix(self):
        assert _normalize_subject("Fw: Hello world") == "Hello world"

    def test_no_prefix_unchanged(self):
        assert _normalize_subject("Hello world") == "Hello world"

    def test_none_returns_empty_string(self):
        assert _normalize_subject(None) == ""

    def test_nested_re_strips_only_outermost(self):
        assert _normalize_subject("Re: Re: Question") == "Re: Question"


# ---------------------------------------------------------------------------
# 2. EmailThread model
# ---------------------------------------------------------------------------


class TestEmailThreadModel:
    def test_str_with_subject(self):
        thread = make_thread(subject="My Thread", external="a@b.com")
        assert "a@b.com" in str(thread)
        assert "My Thread" in str(thread)

    def test_str_with_empty_subject(self):
        thread = make_thread(subject="", external="a@b.com")
        assert "(no subject)" in str(thread)

    def test_default_ordering_newest_first(self):
        old = make_thread(subject="Old", offset_seconds=100)
        new = make_thread(subject="New", offset_seconds=0)
        pks = list(EmailThread.objects.values_list("pk", flat=True))
        assert pks.index(new.pk) < pks.index(old.pk)

    def test_has_unread_defaults_true(self):
        thread = make_thread()
        assert thread.has_unread is True

    def test_has_unread_can_be_cleared(self):
        thread = make_thread(unread=True)
        thread.has_unread = False
        thread.save(update_fields=["has_unread"])
        thread.refresh_from_db()
        assert thread.has_unread is False


# ---------------------------------------------------------------------------
# 3. SentEmail model
# ---------------------------------------------------------------------------


class TestSentEmailModel:
    def test_str_contains_recipient(self):
        c, u = editor_client()
        thread = make_thread()
        sent = make_sent(thread, u)
        assert thread.external_address in str(sent)


# ---------------------------------------------------------------------------
# 4. Auth / permission guards
# ---------------------------------------------------------------------------


class TestInboxAuth:
    """All three inbox views require login + chief_editor permission."""

    def _anon(self):
        return Client()

    def test_inbox_list_redirects_anonymous(self):
        r = self._anon().get(reverse("backend:inbox"))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_inbox_list_forbids_non_editor(self):
        c, _ = plain_client()
        r = c.get(reverse("backend:inbox"))
        assert r.status_code == 403

    def test_inbox_thread_redirects_anonymous(self):
        thread = make_thread()
        r = self._anon().get(reverse("backend:inbox_thread", args=[thread.pk]))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_inbox_thread_forbids_non_editor(self):
        thread = make_thread()
        c, _ = plain_client()
        r = c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        assert r.status_code == 403

    def test_inbox_reply_redirects_anonymous(self):
        thread = make_thread()
        r = self._anon().post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "hi"})
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_inbox_reply_forbids_non_editor(self):
        thread = make_thread()
        c, _ = plain_client()
        r = c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "hi"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 5. inbox view — list
# ---------------------------------------------------------------------------


class TestInboxView:
    def test_empty_inbox_returns_200(self):
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"))
        assert r.status_code == 200

    def test_threads_appear_in_response(self):
        make_thread(subject="Newsletter question", external="reader@example.com")
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"))
        assert b"reader@example.com" in r.content

    def test_unread_thread_rendered(self):
        make_thread(unread=True)
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"))
        assert r.status_code == 200
        # The unread dot uses bg-primary; read uses bg-transparent
        assert b"bg-primary" in r.content

    def test_read_thread_has_no_unread_dot(self):
        make_thread(unread=False)
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"))
        assert b"bg-primary" not in r.content

    def test_newest_thread_appears_before_older(self):
        make_thread(subject="Old thread", offset_seconds=300)
        make_thread(subject="New thread", offset_seconds=0)
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"))
        content = r.content.decode()
        assert content.index("New thread") < content.index("Old thread")

    def test_search_filters_threads_by_subject_and_address(self):
        make_thread(subject="Question about billing", external="reader@example.com")
        make_thread(subject="Another topic", external="someone@else.com")
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"), {"q": "billing"})
        content = r.content.decode()
        assert "Question about billing" in content
        assert "Another topic" not in content

    def test_htmx_search_returns_partial_markup(self):
        make_thread(subject="Newsletter question", external="reader@example.com")
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"), {"q": "reader"}, HTTP_HX_REQUEST="true")
        assert r.status_code == 200
        content = r.content.decode()
        assert "reader@example.com" in content
        assert "<h4" not in content

    def test_empty_search_state_mentions_filter(self):
        make_thread(subject="Newsletter question", external="reader@example.com")
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox"), {"q": "nomatch"})
        assert b"No messages match this filter." in r.content


# ---------------------------------------------------------------------------
# 6. inbox_thread view
# ---------------------------------------------------------------------------


class TestInboxThreadView:
    def test_returns_200_for_existing_thread(self):
        thread = make_thread()
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        assert r.status_code == 200

    def test_returns_404_for_missing_thread(self):
        c, _ = editor_client()
        r = c.get(reverse("backend:inbox_thread", args=[99999]))
        assert r.status_code == 404

    def test_marks_thread_as_read_on_visit(self):
        thread = make_thread(unread=True)
        make_inbound(thread, read=False)
        c, _ = editor_client()
        c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        thread.refresh_from_db()
        assert thread.has_unread is False
        assert InboundEmail.objects.filter(thread=thread, read=False).count() == 0

    def test_already_read_thread_stays_read(self):
        thread = make_thread(unread=False)
        c, _ = editor_client()
        # Should not error or flip to unread
        r = c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        assert r.status_code == 200
        thread.refresh_from_db()
        assert thread.has_unread is False

    def test_timeline_shows_inbound_and_sent_messages(self):
        c, u = editor_client()
        thread = make_thread()
        make_inbound(thread, body="Original question", seconds_ago=60)
        make_sent(thread, u, body="Our reply here")
        r = c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        content = r.content.decode()
        assert "Original question" in content
        assert "Our reply here" in content

    def test_sent_message_appears_after_earlier_inbound(self):
        """Inbound (60s ago) should appear before sent (now) in timeline."""
        c, u = editor_client()
        thread = make_thread()
        make_inbound(thread, body="Came first", seconds_ago=60)
        make_sent(thread, u, body="Came second")
        r = c.get(reverse("backend:inbox_thread", args=[thread.pk]))
        content = r.content.decode()
        assert content.index("Came first") < content.index("Came second")


class TestInboxMarkAllRead:
    def test_marks_all_visible_threads_and_messages_read(self):
        thread_one = make_thread(unread=True, subject="One", external="one@example.com")
        thread_two = make_thread(unread=True, subject="Two", external="two@example.com")
        make_inbound(thread_one, read=False)
        make_inbound(thread_two, read=False)
        c, _ = editor_client()

        r = c.post(reverse("backend:inbox_mark_all_read"))

        assert r.status_code == 302
        thread_one.refresh_from_db()
        thread_two.refresh_from_db()
        assert thread_one.has_unread is False
        assert thread_two.has_unread is False
        assert InboundEmail.objects.filter(thread__in=[thread_one, thread_two], read=False).count() == 0

    def test_marks_only_filtered_threads_when_query_present(self):
        matching = make_thread(unread=True, subject="Billing question", external="reader@example.com")
        other = make_thread(unread=True, subject="Clinical note", external="other@example.com")
        make_inbound(matching, read=False)
        make_inbound(other, read=False)
        c, _ = editor_client()

        c.post(reverse("backend:inbox_mark_all_read"), {"q": "Billing"})

        matching.refresh_from_db()
        other.refresh_from_db()
        assert matching.has_unread is False
        assert other.has_unread is True
        assert InboundEmail.objects.filter(thread=matching, read=False).count() == 0
        assert InboundEmail.objects.filter(thread=other, read=False).count() == 1

    def test_htmx_mark_all_read_returns_partial(self):
        thread = make_thread(unread=True, subject="Question", external="reader@example.com")
        make_inbound(thread, read=False)
        c, _ = editor_client()

        r = c.post(reverse("backend:inbox_mark_all_read"), {"q": "reader"}, HTTP_HX_REQUEST="true")

        assert r.status_code == 200
        assert b"<h4" not in r.content
        assert b'hx-swap-oob="outerHTML"' in r.content
        thread.refresh_from_db()
        assert thread.has_unread is False


# ---------------------------------------------------------------------------
# 7. inbox_reply view
# ---------------------------------------------------------------------------


class TestInboxReply:
    def test_creates_sent_email_record(self):
        c, u = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "My reply"})
        assert SentEmail.objects.filter(thread=thread, sent_by=u).count() == 1

    def test_sent_email_body_matches_input(self):
        c, u = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Specific body text"})
        sent = SentEmail.objects.get(thread=thread)
        assert sent.body == "Specific body text"

    def test_reply_subject_prefixed_with_re(self):
        c, u = editor_client()
        thread = make_thread(subject="Question about the issue")
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Answer"})
        sent = SentEmail.objects.get(thread=thread)
        assert sent.subject.startswith("Re:")

    def test_reply_subject_not_double_prefixed(self):
        """If thread.subject already starts with Re:, don't add another."""
        c, u = editor_client()
        thread = make_thread(subject="Re: Already prefixed")
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Answer"})
        sent = SentEmail.objects.get(thread=thread)
        assert not sent.subject.lower().startswith("re: re:")

    def test_empty_body_does_not_send_or_create_record(self):
        c, _ = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send") as mock_send:
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "   "})
        mock_send.assert_not_called()
        assert SentEmail.objects.filter(thread=thread).count() == 0

    def test_failed_send_does_not_create_sent_email(self):
        c, _ = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send", side_effect=Exception("SMTP error")):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "My reply"})
        assert SentEmail.objects.filter(thread=thread).count() == 0

    def test_reply_updates_thread_last_message_at(self):
        c, _ = editor_client()
        thread = make_thread(offset_seconds=3600)
        old_ts = thread.last_message_at
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Reply"})
        thread.refresh_from_db()
        assert thread.last_message_at > old_ts

    def test_reply_redirects_to_thread(self):
        c, _ = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            r = c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Reply"})
        assert r.status_code == 302
        assert reverse("backend:inbox_thread", args=[thread.pk]) in r["Location"]

    def test_message_id_stored_on_sent_email(self):
        """SentEmail.message_id is set so future In-Reply-To can link threads."""
        c, _ = editor_client()
        thread = make_thread()
        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Reply"})
        sent = SentEmail.objects.get(thread=thread)
        assert sent.message_id.startswith("<")
        assert "@" in sent.message_id

    def test_reply_uses_backend_preference_sender(self):
        c, _ = editor_client()
        thread = make_thread()
        preference = BackendPreference.objects.create(singleton=1)
        preference.inbox_from_name = "Journal Watch Admin"
        preference.inbox_from_address = "admin@journalwatch.org.au"
        preference.save(update_fields=["inbox_from_name", "inbox_from_address"])

        c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Reply"})

        assert mail.outbox[-1].from_email == "Journal Watch Admin <admin@journalwatch.org.au>"

    def test_reply_message_id_domain_uses_backend_preference_address(self):
        c, _ = editor_client()
        thread = make_thread()
        preference = BackendPreference.objects.create(singleton=1)
        preference.inbox_from_name = "Inbox Team"
        preference.inbox_from_address = "team@journalwatch.org.au"
        preference.save(update_fields=["inbox_from_name", "inbox_from_address"])

        with patch("django.core.mail.EmailMessage.send", return_value=1):
            c.post(reverse("backend:inbox_reply", args=[thread.pk]), {"body": "Reply"})

        sent = SentEmail.objects.get(thread=thread)
        assert sent.message_id.endswith("@journalwatch.org.au>")


class TestInboxSenderSettings:
    def test_settings_page_renders_inbox_sender_section(self):
        c, _ = editor_client()

        r = c.get(reverse("backend:backend_settings"))

        assert r.status_code == 200
        content = r.content.decode()
        assert "Inbox sender" in content
        assert "John Smith &lt;john.smith@domain.com&gt;" in content

    def test_save_inbox_sender_settings_updates_backend_preference(self):
        c, _ = editor_client()

        r = c.post(
            reverse("backend:save_inbox_sender_settings"),
            {
                "inbox_from_name": "Journal Watch Admin",
                "inbox_from_address": "admin@journalwatch.org.au",
            },
        )

        assert r.status_code == 302
        preference = BackendPreference.get_solo()
        assert preference is not None
        assert preference.inbox_from_name == "Journal Watch Admin"
        assert preference.inbox_from_address == "admin@journalwatch.org.au"


# ---------------------------------------------------------------------------
# 8. Context processor — inbox_unread_count
# ---------------------------------------------------------------------------


class TestInboxContextProcessorUnreadCount:
    def _make_request(self, user):
        factory = RequestFactory()
        request = factory.get("/editorial/")
        request.user = user
        request.session = {}
        request.resolver_match = None
        return request

    def test_chief_editor_sees_unread_count(self):
        make_thread(unread=True)
        make_thread(unread=True)
        make_thread(unread=False)
        user = UserFactory()
        _grant(user, CHIEF_EDITOR)
        ctx = selected_issue(self._make_request(user))
        assert ctx["inbox_unread_count"] == 2

    def test_non_editor_gets_zero(self):
        make_thread(unread=True)
        user = UserFactory()  # no permissions
        ctx = selected_issue(self._make_request(user))
        assert ctx["inbox_unread_count"] == 0

    def test_count_zero_when_all_read(self):
        make_thread(unread=False)
        make_thread(unread=False)
        user = UserFactory()
        _grant(user, CHIEF_EDITOR)
        ctx = selected_issue(self._make_request(user))
        assert ctx["inbox_unread_count"] == 0

    def test_count_reflects_thread_marked_read(self):
        thread = make_thread(unread=True)
        user = UserFactory()
        _grant(user, CHIEF_EDITOR)

        ctx_before = selected_issue(self._make_request(user))
        assert ctx_before["inbox_unread_count"] == 1

        thread.has_unread = False
        thread.save(update_fields=["has_unread"])

        ctx_after = selected_issue(self._make_request(user))
        assert ctx_after["inbox_unread_count"] == 0

    def test_anonymous_user_returns_empty_dict(self):
        """selected_issue returns {} for anonymous — no inbox_unread_count key."""
        factory = RequestFactory()
        request = factory.get("/")
        request.user = AnonymousUser()
        request.session = {}
        result = selected_issue(request)
        assert result == {}
