"""Send preview copies of all email templates to mailpit for visual review."""

from django.core import mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from spanza_journal_watch.newsletter.models import Newsletter, Subscriber


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

        # 7. Chief editor invite
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
