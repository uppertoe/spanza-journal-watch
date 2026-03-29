#!/usr/bin/env python3
"""
deploy/bootstrap/aws_setup.py — Provision AWS resources for a Journal Watch deployment.

Idempotent — safe to run multiple times. Skips resources that already exist.

Requirements:
  pip install boto3          (or: run inside the Django container)

Run with a named AWS profile (credentials stay in ~/.aws, never in env):
  python deploy/bootstrap/aws_setup.py --profile jw-admin --bucket my-jw-bucket --domain yourdomain.com

For staging, pass --suffix to namespace IAM users, SNS topic, and config set,
and --ses-domain to reuse the existing prod SES identity (already verified):
  python deploy/bootstrap/aws_setup.py --profile jw-admin --bucket jw-staging \\
      --domain staging.journalwatch.org.au --ses-domain journalwatch.org.au \\
      --suffix staging

Works with any credential source boto3 supports — long-lived keys in ~/.aws/credentials,
or short-lived SSO tokens after `aws sso login --profile jw-admin`. Falls back to the
default profile / environment variables if --profile is omitted.

What this script does:
  - Creates the S3 bucket with versioning, block-public-access, and lifecycle rules
  - Creates three scoped IAM users: jw-django[-suffix], jw-planka[-suffix], jw-backup[-suffix]
  - Attaches least-privilege inline policies to each
  - Generates access keys and prints them for .env and the server repo backup layer
  - Creates the SES email identity (ses-domain) and prints DKIM DNS records
    (skipped when --ses-domain differs from --domain — identity already exists)
  - Creates the SES configuration set (TrackingConfigSet[-suffix])
  - Creates the SNS topic for bounce/complaint events (journalwatch-ses-events[-suffix])
  - Wires SES → SNS for Bounce + Complaint event types
  - Prints the SNS subscription command to run once the app is live

What requires manual steps:
  - Adding the DNS records this script outputs
  - Requesting SES production access (once, via AWS console)
  - Running the SNS subscription once Django is publicly reachable
  - Creating SES SMTP credentials if your server-side backup workflow needs them
"""

import argparse
import json
import sys
import textwrap

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 is not installed. Run: pip install boto3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def skip(msg):
    print(f"  {YELLOW}–{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}!{RESET} {msg}")


def err(msg):
    print(f"  {RED}✗{RESET} {msg}")


def section(title):
    print(f"\n{BOLD}{title}{RESET}")
    print("─" * len(title))


def box(title, lines):
    width = max(len(title), max(len(line) for line in lines)) + 4
    print(f"\n  ┌{'─' * width}┐")
    print(f"  │ {BOLD}{title}{RESET}{' ' * (width - len(title) - 1)}│")
    print(f"  ├{'─' * width}┤")
    for line in lines:
        print(f"  │ {line}{' ' * (width - len(line) - 1)}│")
    print(f"  └{'─' * width}┘")


# ---------------------------------------------------------------------------
# IAM policy documents
# ---------------------------------------------------------------------------


def django_policy(bucket):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3MediaReadWrite",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{bucket}/media/*",
            },
            {
                "Sid": "S3InboundEmailRead",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{bucket}/email/*",
            },
            {
                "Sid": "S3ListBucket",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket}",
                "Condition": {"StringLike": {"s3:prefix": ["media/*", "email/*"]}},
            },
            {
                "Sid": "SESSend",
                "Effect": "Allow",
                "Action": ["ses:SendEmail", "ses:SendRawEmail"],
                "Resource": "*",
            },
        ],
    }


def planka_policy(bucket):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3PlankaBucketObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{bucket}/*",
            },
            {
                "Sid": "S3ListBucket",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket}",
            },
        ],
    }


def backup_policy(bucket):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3BackupReadWrite",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{bucket}/*",
            },
            {
                "Sid": "S3ListBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": f"arn:aws:s3:::{bucket}",
            },
        ],
    }


def media_public_read_policy(bucket):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadMedia",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/media/*",
            }
        ],
    }


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------


def setup_s3(
    s3,
    bucket,
    region,
    *,
    enable_media_public_read=True,
    backup_noncurrent_expiration_days=0,
    enable_versioning=True,
):
    section("S3 bucket")

    # Create bucket
    try:
        kwargs = {"Bucket": bucket}
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        ok(f"Created bucket: {bucket}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            skip(f"Bucket already exists: {bucket}")
        else:
            raise

    # Block all public access
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
    ok("Public ACLs blocked; bucket policy access allowed")

    if enable_media_public_read:
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(media_public_read_policy(bucket)))
        ok("Bucket policy: public read enabled for media/* only")

    # Versioning
    if enable_versioning:
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        ok("Versioning: enabled")
    else:
        skip("Versioning: left disabled")

    # Default encryption (SSE-S3)
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    ok("Encryption: SSE-S3 (AES-256)")

    # Lifecycle rules
    rules = [
        {
            "ID": "expire-incomplete-multipart",
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
        }
    ]
    if backup_noncurrent_expiration_days and backup_noncurrent_expiration_days > 0:
        rules.append(
            {
                "ID": "expire-old-backup-versions",
                "Status": "Enabled",
                "Filter": {"Prefix": "backups/"},
                "NoncurrentVersionExpiration": {"NoncurrentDays": backup_noncurrent_expiration_days},
            }
        )

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": rules},
    )
    if backup_noncurrent_expiration_days and backup_noncurrent_expiration_days > 0:
        ok(
            "Lifecycle rules: incomplete multipart (7d) + old backup versions "
            f"({backup_noncurrent_expiration_days}d)"
        )
    else:
        ok("Lifecycle rules: incomplete multipart uploads expire after 7d")


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------


def create_iam_user(iam, username, policy_name, policy_doc):
    """Creates user + policy. Returns (access_key, secret_key) or None if already existed."""
    created = False
    try:
        iam.create_user(UserName=username)
        ok(f"Created IAM user: {username}")
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            skip(f"IAM user already exists: {username}")
        else:
            raise

    # Always re-apply the policy (idempotent update)
    iam.put_user_policy(
        UserName=username,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_doc),
    )
    ok(f"  Policy applied: {policy_name}")

    if not created:
        # Check existing keys
        keys = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
        if keys:
            warn(f"  User already has {len(keys)} access key(s) — skipping key creation.")
            warn("  If you need new keys, delete existing ones via the AWS console first.")
            return None

    key = iam.create_access_key(UserName=username)["AccessKey"]
    ok(f"  Access key created: {key['AccessKeyId']}")
    return key["AccessKeyId"], key["SecretAccessKey"]


def setup_iam(iam, bucket, planka_bucket, backup_bucket, suffix=""):
    section("IAM users")
    keys = {}
    sfx = f"-{suffix}" if suffix else ""

    result = create_iam_user(iam, f"jw-django{sfx}", f"jw-django{sfx}-policy", django_policy(bucket))
    if result:
        keys["django"] = result

    result = create_iam_user(iam, f"jw-planka{sfx}", f"jw-planka{sfx}-policy", planka_policy(planka_bucket))
    if result:
        keys["planka"] = result

    result = create_iam_user(iam, f"jw-backup{sfx}", f"jw-backup{sfx}-policy", backup_policy(backup_bucket))
    if result:
        keys["backup"] = result

    return keys


# ---------------------------------------------------------------------------
# SES
# ---------------------------------------------------------------------------


def setup_ses(ses_v2, ses_domain, config_set_name="TrackingConfigSet"):
    section("SES email identity")
    dkim_tokens = None

    try:
        resp = ses_v2.create_email_identity(
            EmailIdentity=ses_domain,
            DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
        )
        dkim_tokens = resp.get("DkimAttributes", {}).get("Tokens", [])
        ok(f"Created SES identity: {ses_domain}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            skip(f"SES identity already exists: {ses_domain}")
            # Fetch existing DKIM tokens
            try:
                resp = ses_v2.get_email_identity(EmailIdentity=ses_domain)
                dkim_tokens = resp.get("DkimAttributes", {}).get("Tokens", [])
            except ClientError:
                pass
        else:
            raise

    section("SES configuration set")
    try:
        ses_v2.create_configuration_set(ConfigurationSetName=config_set_name)
        ok(f"Created configuration set: {config_set_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            skip(f"Configuration set already exists: {config_set_name}")
        else:
            raise

    return dkim_tokens


# ---------------------------------------------------------------------------
# SNS
# ---------------------------------------------------------------------------


def setup_sns(sns, ses_v2, region, account_id, domain, webhook_secret, suffix="", config_set_name="TrackingConfigSet"):
    section("SNS topic")
    sfx = f"-{suffix}" if suffix else ""
    topic_name = f"journalwatch-ses-events{sfx}"

    resp = sns.create_topic(Name=topic_name)
    topic_arn = resp["TopicArn"]
    ok(f"Topic ready: {topic_arn}")

    # Wire SES → SNS for Bounce + Complaint
    section("SES → SNS event destination")
    dest_name = f"BounceComplaintSNS{sfx}"
    try:
        ses_v2.create_configuration_set_event_destination(
            ConfigurationSetName=config_set_name,
            EventDestinationName=dest_name,
            EventDestination={
                "Enabled": True,
                "MatchingEventTypes": ["BOUNCE", "COMPLAINT"],
                "SnsDestination": {"TopicArn": topic_arn},
            },
        )
        ok(f"Event destination created: {dest_name} → {topic_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            skip(f"Event destination already exists: {dest_name}")
        else:
            raise

    return topic_arn


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def print_credentials(keys, bucket, planka_bucket, backup_bucket, region, config_set_name, webhook_secret=""):
    if not keys:
        return

    section("Generated credentials — save these now")
    print("  These are shown once. Add them to the locations indicated.\n")

    if "django" in keys:
        ak, sk = keys["django"]
        webhook_value = webhook_secret or "GENERATE-AND-ADD-A-SECRET"
        box(
            ".env  →  AWS / SES",
            [
                f"DJANGO_AWS_ACCESS_KEY_ID={ak}",
                f"DJANGO_AWS_SECRET_ACCESS_KEY={sk}",
                f"DJANGO_AWS_STORAGE_BUCKET_NAME={bucket}",
                f"DJANGO_AWS_S3_REGION_NAME={region}",
                f"DJANGO_AWS_DEFAULT_REGION={region}",
                "DJANGO_ANYMAIL_INBOUND_S3_OBJECT_PREFIX=email",
                f"WEBHOOK_SECRET={webhook_value}",
                f"ANYMAIL_CONFIGURATION_SET_NAME={config_set_name}",
            ],
        )
        print("  WEBHOOK_SECRET is chosen by you, not AWS.")
        print("  Use the same value in .env and in the aws sns subscribe URL shown below.")
        print("  Django will not receive SNS events until you create that HTTPS subscription.\n")

    if "planka" in keys:
        ak, sk = keys["planka"]
        box(
            ".env  →  Planka S3",
            [
                f"PLANKA_S3_BUCKET={planka_bucket}",
                f"PLANKA_S3_ACCESS_KEY_ID={ak}",
                f"PLANKA_S3_SECRET_ACCESS_KEY={sk}",
                f"PLANKA_S3_REGION={region}",
            ],
        )

    if "backup" in keys:
        ak, sk = keys["backup"]
        box(
            "/etc/restic/env  →  Restic backups",
            [
                f"RESTIC_REPOSITORY=s3:s3.amazonaws.com/{backup_bucket}",
                f"AWS_ACCESS_KEY_ID={ak}",
                f"AWS_SECRET_ACCESS_KEY={sk}",
                f"AWS_DEFAULT_REGION={region}",
            ],
        )
        print("  RESTIC_PASSWORD is generated by you or by your server-repo backup setup, not by AWS.")
        print("  SES SMTP credentials for backup notification emails are separate from these IAM keys.\n")


def print_dns_records(ses_domain, dkim_tokens, region, reusing_identity=False):
    section("DNS records to add")

    if reusing_identity:
        print(f"  SES identity ({ses_domain}) is already verified — no new DNS records needed.\n")
        return

    print("  Add these to your DNS provider. SES will not send until they are verified.\n")

    records = [
        f"  {'Type':<8}  {'Name':<50}  Value",
        f"  {'─'*8}  {'─'*50}  {'─'*40}",
        f"  {'TXT':<8}  {'_amazonses.' + ses_domain:<50}  (shown in SES console after identity creation)",
    ]

    if dkim_tokens:
        for token in dkim_tokens:
            name = f"{token}._domainkey.{ses_domain}"
            value = f"{token}.dkim.amazonses.com"
            records.append(f"  {'CNAME':<8}  {name:<50}  {value}")
    else:
        records.append(f"  CNAME   (3 × DKIM records — check SES console → Verified identities → {ses_domain})")

    records += [
        "",
        "  Optional — inbound email only:",
        f"  {'MX':<8}  {ses_domain:<50}  10 inbound-smtp.{region}.amazonaws.com",
    ]

    for r in records:
        print(r)


def print_manual_steps(domain, region, topic_arn, webhook_secret):
    section("Manual steps remaining")

    steps = [
        ("1. Add DNS records", "Add the records printed above. SES verifies within minutes."),
        (
            "2. Request SES production access",
            f"https://console.aws.amazon.com/ses/home?region={region}#/account\n"
            "     New accounts are in the sandbox (verified addresses only) until approved.",
        ),
        (
            "3. Subscribe SNS to the Django webhook",
            "Run this once your app is publicly reachable.\n"
            "     This is the step that connects the SNS topic to Django.\n"
            "     Use the same WEBHOOK_SECRET value that you placed in .env.\n\n"
            f"     aws sns subscribe \\\n"
            f"       --region {region} \\\n"
            f"       --topic-arn {topic_arn} \\\n"
            f"       --protocol https \\\n"
            f"       --notification-endpoint 'https://{domain}/anymail/amazon_ses/tracking/?secret={webhook_secret}'\n\n"
            "     Django auto-confirms the subscription. Check SNS console for 'Confirmed' status.",
        ),
        (
            "4. Create SES SMTP credentials (for backup email notifications)",
            f"https://console.aws.amazon.com/ses/home?region={region}#/smtp-settings\n"
            "     Click 'Create SMTP credentials'. Add the output to /etc/restic/env.",
        ),
    ]

    for title, detail in steps:
        print(f"\n  {BOLD}{title}{RESET}")
        for line in detail.splitlines():
            print(f"     {line}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def provision(
    s3,
    iam,
    ses_v2,
    sns,
    bucket,
    planka_bucket,
    backup_bucket,
    region,
    domain,
    account_id,
    webhook_secret,
    suffix="",
    ses_domain=None,
    backup_noncurrent_expiration_days=0,
):
    """
    Run all provisioning steps and return (keys, dkim_tokens, topic_arn).

    All parameters are pre-built boto3 clients so callers (and tests) can
    inject mocks without monkey-patching module-level names.

    suffix    — appended to IAM usernames, SNS topic, and config set name
                (e.g. "staging" → jw-django-staging, TrackingConfigSet-staging)
    ses_domain — domain used for the SES identity; defaults to domain.
                 Set to the prod domain when reusing an existing SES identity
                 (e.g. staging deployment that shares journalwatch.org.au).
    """
    ses_domain = ses_domain or domain
    config_set_name = f"TrackingConfigSet-{suffix}" if suffix else "TrackingConfigSet"

    setup_s3(s3, bucket, region, backup_noncurrent_expiration_days=backup_noncurrent_expiration_days)
    if planka_bucket != bucket:
        setup_s3(
            s3,
            planka_bucket,
            region,
            enable_media_public_read=False,
            backup_noncurrent_expiration_days=0,
        )
    if backup_bucket not in {bucket, planka_bucket}:
        setup_s3(
            s3,
            backup_bucket,
            region,
            enable_media_public_read=False,
            backup_noncurrent_expiration_days=0,
            enable_versioning=False,
        )
    keys = setup_iam(iam, bucket, planka_bucket, backup_bucket, suffix=suffix)
    dkim_tokens = setup_ses(ses_v2, ses_domain, config_set_name)
    topic_arn = setup_sns(
        sns, ses_v2, region, account_id, domain, webhook_secret, suffix=suffix, config_set_name=config_set_name
    )
    return keys, dkim_tokens, topic_arn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Provision AWS resources for Journal Watch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              # Production
              python deploy/bootstrap/aws_setup.py --profile jw-admin \\
                  --bucket jw-prod --planka-bucket jw-prod-planka \\
                  --backup-bucket jw-prod-backups --domain journalwatch.org.au

              # Staging — reuses the existing SES identity (journalwatch.org.au is already verified)
              python deploy/bootstrap/aws_setup.py --profile jw-admin \\
                  --bucket jw-staging --planka-bucket jw-staging-planka \\
                  --backup-bucket jw-staging-backups \\
                  --domain staging.journalwatch.org.au --ses-domain journalwatch.org.au \\
                  --suffix staging

            Profile setup (one-time):
              aws configure --profile jw-admin            # long-lived key
              aws configure sso --profile jw-admin        # SSO (recommended)
              aws sso login --profile jw-admin            # refresh SSO token before running
        """),
    )
    p.add_argument("--bucket", required=True, help="S3 bucket name (will be created)")
    p.add_argument(
        "--planka-bucket",
        default=None,
        dest="planka_bucket",
        help="Dedicated S3 bucket for Planka objects. Defaults to <bucket>-planka.",
    )
    p.add_argument(
        "--backup-bucket",
        default=None,
        dest="backup_bucket",
        help="Dedicated S3 bucket for Restic backups. Defaults to <bucket>-backups.",
    )
    p.add_argument(
        "--domain", required=True, help="App domain used for the SNS webhook URL (e.g. staging.journalwatch.org.au)"
    )
    p.add_argument("--region", default="ap-southeast-2", help="AWS region (default: ap-southeast-2)")
    p.add_argument(
        "--profile",
        default=None,
        help="AWS named profile from ~/.aws/config (e.g. jw-admin). " "Omit to use the default credential chain.",
    )
    p.add_argument(
        "--webhook-secret",
        default="",
        help="WEBHOOK_SECRET from .env — used to print the SNS subscription URL",
    )
    p.add_argument(
        "--suffix",
        default="",
        help="Suffix appended to IAM usernames, SNS topic, and SES config set "
        "(e.g. 'staging' → jw-django-staging, TrackingConfigSet-staging). "
        "Omit for production.",
    )
    p.add_argument(
        "--ses-domain",
        default=None,
        dest="ses_domain",
        help="Domain for the SES identity. Defaults to --domain. "
        "Set to the prod domain (e.g. journalwatch.org.au) when the SES identity "
        "is already verified and you just need new IAM users / SNS topic.",
    )
    p.add_argument(
        "--backup-noncurrent-expiration-days",
        type=int,
        default=0,
        dest="backup_noncurrent_expiration_days",
        help="Expire noncurrent object versions under backups/ after N days. "
        "Default: 0 (disabled; useful when Restic already manages snapshots).",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.planka_bucket:
        args.planka_bucket = f"{args.bucket}-planka"
    if not args.backup_bucket:
        args.backup_bucket = f"{args.bucket}-backups"

    ses_domain = args.ses_domain or args.domain
    reusing_identity = ses_domain != args.domain
    config_set_name = f"TrackingConfigSet-{args.suffix}" if args.suffix else "TrackingConfigSet"

    print(f"\n{BOLD}Journal Watch — AWS provisioning{RESET}")
    print(f"  App bucket:   {args.bucket}")
    print(f"  Planka bucket:{' ' if len(args.planka_bucket) >= len(args.bucket) else '  '}{args.planka_bucket}")
    print(f"  Backup bucket:{' ' if len(args.backup_bucket) >= len(args.bucket) else '  '}{args.backup_bucket}")
    print(f"  App domain:   {args.domain}")
    print(f"  SES identity: {ses_domain}{' (reusing existing)' if reusing_identity else ''}")
    print(f"  Config set:   {config_set_name}")
    print(f"  Region:       {args.region}")
    if args.suffix:
        print(f"  Suffix:       {args.suffix}")
    if args.profile:
        print(f"  Profile:      {args.profile}")

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        s3 = session.client("s3")
        iam = session.client("iam")
        ses_v2 = session.client("sesv2")
        sns = session.client("sns")
        sts = session.client("sts")

        account_id = sts.get_caller_identity()["Account"]
        ok(f"Connected — account: {account_id}")
    except ClientError as e:
        sys.exit(f"\nAWS authentication failed: {e}")

    try:
        keys, dkim_tokens, topic_arn = provision(
            s3,
            iam,
            ses_v2,
            sns,
            args.bucket,
            args.planka_bucket,
            args.backup_bucket,
            args.region,
            args.domain,
            account_id,
            args.webhook_secret,
            suffix=args.suffix,
            ses_domain=ses_domain,
            backup_noncurrent_expiration_days=args.backup_noncurrent_expiration_days,
        )
    except ClientError as e:
        sys.exit(f"\n{RED}AWS error:{RESET} {e}")

    print_credentials(
        keys,
        args.bucket,
        args.planka_bucket,
        args.backup_bucket,
        args.region,
        config_set_name,
        webhook_secret=args.webhook_secret,
    )
    print_dns_records(ses_domain, dkim_tokens, args.region, reusing_identity=reusing_identity)
    print_manual_steps(args.domain, args.region, topic_arn, args.webhook_secret or "YOUR-WEBHOOK-SECRET")

    print(f"\n{GREEN}{BOLD}Done.{RESET}\n")


if __name__ == "__main__":
    main()
