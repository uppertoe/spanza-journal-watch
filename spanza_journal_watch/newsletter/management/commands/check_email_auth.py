"""
Management command to verify SPF, DKIM, and DMARC DNS records for the
newsletter sending domain.

Usage:
    python manage.py check_email_auth
    python manage.py check_email_auth --domain journalwatch.org.au
"""

import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand


def _query_txt(name):
    """Query TXT records using nslookup (available on all platforms)."""
    try:
        result = subprocess.run(
            ["nslookup", "-type=TXT", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _query_cname(name):
    """Query CNAME records."""
    try:
        result = subprocess.run(
            ["nslookup", "-type=CNAME", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


class Command(BaseCommand):
    help = "Check SPF, DKIM, and DMARC DNS records for newsletter email authentication"

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            default=None,
            help="Domain to check (default: extracted from NEWSLETTER_FROM_EMAIL)",
        )

    def handle(self, *args, **options):
        domain = options["domain"]
        if not domain:
            from_email = getattr(settings, "NEWSLETTER_FROM_EMAIL", "")
            domain = from_email.rsplit("@", 1)[-1].rstrip(">").strip()
        if not domain:
            self.stderr.write(self.style.ERROR("Could not determine domain. Use --domain flag."))
            return

        self.stdout.write(f"\nChecking email authentication for: {self.style.SUCCESS(domain)}\n")
        self.stdout.write("=" * 60)

        self._check_spf(domain)
        self._check_dkim(domain)
        self._check_dmarc(domain)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            "\nFor Amazon SES setup, ensure:\n"
            "  1. Domain is verified in SES console\n"
            "  2. DKIM CNAME records from SES are published\n"
            "  3. SPF includes amazonses.com\n"
            "  4. DMARC is set to at least p=none with reporting\n"
        )

    def _check_spf(self, domain):
        self.stdout.write(f"\n{'SPF':=^60}")
        output = _query_txt(domain)
        if "v=spf1" in output:
            self.stdout.write(f"  {self.style.SUCCESS('FOUND')}: SPF record exists")
            if "amazonses" in output.lower() or "amazon" in output.lower():
                self.stdout.write(f"  {self.style.SUCCESS('OK')}: Amazon SES is included")
            else:
                self.stdout.write(
                    f"  {self.style.WARNING('WARNING')}: amazonses.com may not be in SPF record.\n"
                    "  Ensure 'include:amazonses.com' is present to authorise SES."
                )
        else:
            self.stdout.write(f"  {self.style.ERROR('MISSING')}: No SPF record found")
            self.stdout.write("  Add a TXT record: v=spf1 include:amazonses.com ~all")

    def _check_dkim(self, domain):
        self.stdout.write(f"\n{'DKIM':=^60}")
        found = False
        for selector in ["default", "selector1", "selector2", "ses"]:
            dkim_domain = f"{selector}._domainkey.{domain}"
            output = _query_txt(dkim_domain) + _query_cname(dkim_domain)
            if "NXDOMAIN" not in output and ("canonical name" in output.lower() or "dkim" in output.lower()):
                self.stdout.write(f"  {self.style.SUCCESS('FOUND')}: DKIM record at {dkim_domain}")
                found = True
                break

        if not found:
            self.stdout.write(
                f"  {self.style.WARNING('UNCLEAR')}: Could not verify DKIM records via common selectors.\n"
                "  SES DKIM uses unique selectors. Check the SES console under\n"
                "  Identity > DKIM to confirm CNAME records are published."
            )

    def _check_dmarc(self, domain):
        self.stdout.write(f"\n{'DMARC':=^60}")
        dmarc_domain = f"_dmarc.{domain}"
        output = _query_txt(dmarc_domain)

        if "v=DMARC1" in output:
            self.stdout.write(f"  {self.style.SUCCESS('FOUND')}: DMARC record exists")
            if "p=reject" in output:
                self.stdout.write(f"  {self.style.SUCCESS('STRONG')}: Policy is reject (strictest)")
            elif "p=quarantine" in output:
                self.stdout.write(f"  {self.style.SUCCESS('GOOD')}: Policy is quarantine")
            elif "p=none" in output:
                self.stdout.write(
                    f"  {self.style.WARNING('MONITOR ONLY')}: Policy is none — "
                    "consider upgrading to quarantine or reject once confident"
                )
            if "rua=" in output:
                self.stdout.write(f"  {self.style.SUCCESS('OK')}: Aggregate reporting enabled")
            else:
                self.stdout.write(
                    f"  {self.style.WARNING('TIP')}: Add rua= for aggregate reports "
                    f"(e.g., rua=mailto:dmarc@{domain})"
                )
        else:
            self.stdout.write(f"  {self.style.ERROR('MISSING')}: No DMARC record found at {dmarc_domain}")
            self.stdout.write(f"  Add a TXT record at {dmarc_domain}: v=DMARC1; p=none; rua=mailto:dmarc@{domain}")
