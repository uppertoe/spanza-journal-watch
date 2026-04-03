"""Send preview copies of all email templates to mailpit for visual review."""

from django.core import mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber
from spanza_journal_watch.users.models import User


class Command(BaseCommand):
    help = "Send preview copies of all email templates to mailpit"

    def add_arguments(self, parser):
        parser.add_argument("--to", default="preview@example.com", help="Recipient address")

    def handle(self, *args, **options):
        to = options["to"]
        emails = []

        # 1. Newsletter
        newsletter = Newsletter.objects.order_by("-pk").first()
        if newsletter:
            subscriber = Subscriber.objects.filter(tester=True).first()
            if not subscriber:
                subscriber = Subscriber(email=to, unsubscribe_token="preview-token")

            ctx = newsletter.get_email_context()
            ctx["subscriber"] = subscriber
            ctx["pixel"] = ""
            ctx["tracker"] = ""
            html = newsletter.generate_html_content(ctx)
            txt = newsletter.generate_txt_content(ctx)
            msg = mail.EmailMultiAlternatives(
                subject=f"[Preview] Newsletter: {newsletter.subject}",
                body=txt,
                to=[to],
            )
            msg.attach_alternative(html, "text/html")
            emails.append(msg)
            self.stdout.write(f"  Newsletter: {newsletter.subject}")
        else:
            self.stdout.write(self.style.WARNING("  No newsletter found, skipping"))

        # 2. Subscription confirmation
        sub_ctx = {
            "subscriber": Subscriber(email=to, unsubscribe_token="preview-token"),
            "tracker": "",
            "image_domain": "http://127.0.0.1:3000",
            "spanza_logo_url": ctx.get("spanza_logo_url", "") if newsletter else "",
            "email_heading_url": ctx.get("email_heading_url", "") if newsletter else "",
        }
        html = render_to_string("newsletter/email_confirmation.html", sub_ctx)
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Subscription confirmation",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Subscription confirmation")

        # 3. Password reset
        html = render_to_string(
            "account/email/password_reset_key_message.html",
            {"password_reset_url": "https://journalwatch.org.au/accounts/password/reset/key/example-token/"},
        )
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Password reset",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Password reset")

        # 4. Unknown account
        html = render_to_string(
            "account/email/unknown_account_message.html",
            {"email": "someone@example.com"},
        )
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Unknown account",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Unknown account")

        # 5. Contributor invite
        html = render_to_string(
            "backend/email/issue_contributor_invite.html",
            {
                "issue": type("Issue", (), {"name": "January 2026"})(),
                "contributor": type("Contributor", (), {"name": "Dr Sarah Chen"})(),
                "accept_url": "https://journalwatch.org.au/editorial/accept-invite/example-token/",
                "expires_at": __import__("datetime").date(2026, 10, 1),
                "docs_url": "https://docs.journalwatch.org.au/",
            },
        )
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Contributor invite",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Contributor invite")

        # 6. Contributor welcome
        html = render_to_string(
            "backend/email/issue_contributor_welcome.html",
            {
                "issue": type("Issue", (), {"name": "January 2026"})(),
                "contributor": type("Contributor", (), {"name": "Dr Sarah Chen"})(),
                "planka_url": "https://planka.journalwatch.org.au/",
                "docs_url": "https://docs.journalwatch.org.au/",
            },
        )
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Contributor welcome",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Contributor welcome")

        # -- Allauth account notifications --
        preview_user = User.objects.first() or User(name="Dr Preview User", email="preview@example.com")
        security_ctx = {
            "ip": "203.0.113.42",
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "timestamp": "3 Apr 2026, 14:30 AEST",
        }

        # 7. Account already exists
        html = render_to_string(
            "account/email/account_already_exists_message.html",
            {
                "email": "user@example.com",
                "password_reset_url": "https://journalwatch.org.au/accounts/password/reset/",
            },
        )
        msg = mail.EmailMultiAlternatives(subject="[Preview] Account already exists", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Account already exists")

        # 8. Email confirmation (code branch)
        html = render_to_string(
            "account/email/email_confirmation_message.html",
            {"user": preview_user, "code": "847293", "activate_url": ""},
        )
        msg = mail.EmailMultiAlternatives(subject="[Preview] Email confirmation (code)", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Email confirmation (code)")

        # 9. Email confirmation (link branch)
        html = render_to_string(
            "account/email/email_confirmation_message.html",
            {
                "user": preview_user,
                "code": "",
                "activate_url": "https://journalwatch.org.au/accounts/confirm-email/example-key/",
            },
        )
        msg = mail.EmailMultiAlternatives(subject="[Preview] Email confirmation (link)", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Email confirmation (link)")

        # 10. Login code
        html = render_to_string("account/email/login_code_message.html", {"code": "529174"})
        msg = mail.EmailMultiAlternatives(subject="[Preview] Login code", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Login code")

        # 11. Password reset code
        html = render_to_string("account/email/password_reset_code_message.html", {"code": "384621"})
        msg = mail.EmailMultiAlternatives(subject="[Preview] Password reset code", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Password reset code")

        # 12. Password changed
        html = render_to_string("account/email/password_changed_message.html", security_ctx)
        msg = mail.EmailMultiAlternatives(subject="[Preview] Password changed", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Password changed")

        # 13. Password set
        html = render_to_string("account/email/password_set_message.html", security_ctx)
        msg = mail.EmailMultiAlternatives(subject="[Preview] Password set", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Password set")

        # 14. Password reset (notification)
        html = render_to_string("account/email/password_reset_message.html", security_ctx)
        msg = mail.EmailMultiAlternatives(subject="[Preview] Password reset (notification)", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Password reset (notification)")

        # 15. Email changed
        html = render_to_string(
            "account/email/email_changed_message.html",
            {**security_ctx, "from_email": "old@example.com", "to_email": "new@example.com"},
        )
        msg = mail.EmailMultiAlternatives(subject="[Preview] Email changed", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Email changed")

        # 16. Email deleted
        html = render_to_string(
            "account/email/email_deleted_message.html",
            {**security_ctx, "deleted_email": "removed@example.com"},
        )
        msg = mail.EmailMultiAlternatives(subject="[Preview] Email deleted", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Email deleted")

        # 17. Email confirmed
        html = render_to_string("account/email/email_confirm_message.html", security_ctx)
        msg = mail.EmailMultiAlternatives(subject="[Preview] Email confirmed", body="Preview", to=[to])
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Email confirmed")

        # 18. Chief editor invite
        html = render_to_string(
            "backend/email/chief_editor_invite.html",
            {
                "invite": type(
                    "Invite", (), {"name": "Dr James Liu", "expires_at": __import__("datetime").date(2026, 10, 1)}
                )(),
                "invited_by": type(
                    "User",
                    (),
                    {"get_full_name": lambda self: "Dr Eamonn Upperton", "email": "admin@journalwatch.org.au"},
                )(),
                "accept_url": "https://journalwatch.org.au/editorial/accept-chief-editor/example-token/",
            },
        )
        msg = mail.EmailMultiAlternatives(
            subject="[Preview] Chief editor invite",
            body="Preview",
            to=[to],
        )
        msg.attach_alternative(html, "text/html")
        emails.append(msg)
        self.stdout.write("  Chief editor invite")

        # Send all
        connection = mail.get_connection()
        connection.send_messages(emails)
        self.stdout.write(self.style.SUCCESS(f"\nSent {len(emails)} preview emails to {to}"))
