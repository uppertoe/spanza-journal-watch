# AWS setup — S3, SES, and SNS

This document covers the complete AWS configuration for a production deployment:
the S3 bucket layout and IAM policies, SES domain setup and SMTP credentials,
and SNS-based bounce/complaint tracking for the Django webhook.

---

## Automated setup

Most of the infrastructure can be provisioned in a single command:

```bash
# Requires admin AWS credentials (not the service credentials in .env)
python deploy/bootstrap/aws_setup.py \
  --bucket your-bucket-name \
  --domain yourdomain.com \
  --webhook-secret "$(grep WEBHOOK_SECRET .env | cut -d= -f2)"
```

Run the isolated AWS provisioning tests with:

```bash
python3 -m pytest -q -o addopts='' deploy/bootstrap/tests/test_aws_setup.py
```

The script is **idempotent** — safe to run multiple times.

### What the script does automatically

| Resource | Action |
|----------|--------|
| S3 bucket | Creates with versioning, block-all-public-access, SSE-S3, lifecycle rules |
| IAM user `jw-django` | Creates with scoped `media/*` + `email/*` + SES send policy |
| IAM user `jw-planka` | Creates with scoped `attachments/*`-only policy |
| IAM user `jw-backup` | Creates with scoped `backups/*`-only policy |
| IAM access keys | Generates one key per new user, printed once for `.env` |
| SES email identity | Creates domain identity and returns DKIM DNS records |
| SES configuration set | Creates `TrackingConfigSet` |
| SNS topic | Creates `journalwatch-ses-events` |
| SES → SNS routing | Wires Bounce + Complaint events to the SNS topic |

### What still requires manual steps

| Step | Why it can't be automated |
|------|--------------------------|
| Add DNS records (TXT + CNAME × 3) | Must be added via your DNS provider |
| SES production access request | AWS reviews manually; ~24h turnaround |
| SNS webhook subscription | Requires Django to be publicly reachable |
| SES SMTP credentials | Must be created via AWS console (for backup email notifications) |

The script prints exact instructions and commands for each of these.

---

## S3 bucket layout

A single bucket is shared by four consumers, each with a distinct prefix:

```
your-bucket/
├── attachments/    ← Planka card attachments (IAM: jw-planka)
├── email/          ← Inbound SES emails, written by SES rule action (IAM: jw-django)
├── media/          ← Django media files — images, CSVs (IAM: jw-django)
│   ├── backend/
│   ├── issues/
│   └── uploads/
└── backups/        ← Restic-encrypted database backups (IAM: jw-backup)
```

Static files are **not** stored in S3 — they are served by Whitenoise directly
from the Django container.

---

## Step 1 — Create the S3 bucket

In the AWS console (or CLI), create one bucket named however you like
(e.g. `journalwatch-prod`).

### Required bucket settings

| Setting | Value |
|---------|-------|
| Block all public access | **On** (all four checkboxes) |
| Versioning | **Enabled** — protects media files from accidental overwrite |
| Default encryption | SSE-S3 (AES-256) or SSE-KMS |
| Object ownership | Bucket owner enforced (disables ACLs) |

> **Note on ACLs**: `MediaRootS3Boto3Storage` does not set an ACL
> (`default_acl` is unset), so no ACL is applied to uploaded objects and
> bucket owner enforced mode works correctly.

### Recommended lifecycle rules

These rules keep the bucket tidy and control backup retention costs:

1. **Expire incomplete multipart uploads** — expire after 7 days (applies to all
   prefixes). Prevents orphaned in-progress uploads accumulating.

2. **Expire old backup versions** — apply to prefix `backups/`, expire
   non-current versions after 90 days.

---

## Step 2 — Create IAM users

Create **three** IAM users with no console access (programmatic access only).
Name them however you like; the names below are suggestions.

### 2a. `jw-django` — app media + SES sending

This user is referenced by `DJANGO_AWS_ACCESS_KEY_ID` / `DJANGO_AWS_SECRET_ACCESS_KEY`.

**Inline policy — `jw-django-policy`:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3MediaReadWrite",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/media/*"
    },
    {
      "Sid": "S3InboundEmailRead",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/email/*"
    },
    {
      "Sid": "S3ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["media/*", "email/*"]
        }
      }
    },
    {
      "Sid": "SESSend",
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail"
      ],
      "Resource": "*"
    }
  ]
}
```

### 2b. `jw-planka` — Planka bucket only

This user is referenced by `PLANKA_S3_ACCESS_KEY_ID` / `PLANKA_S3_SECRET_ACCESS_KEY`.
It should use a dedicated bucket referenced by `PLANKA_S3_BUCKET`.

**Inline policy — `jw-planka-policy`:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3PlankaBucketObjects",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::YOUR-PLANKA-BUCKET/*"
    },
    {
      "Sid": "S3ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::YOUR-PLANKA-BUCKET"
    }
  ]
}
```

### 2c. `jw-backup` — Restic backups only

This user's credentials belong to the server repo / VPS backup layer, not to
any Journal Watch Docker service.

**Inline policy — `jw-backup-policy`:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3BackupReadWrite",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/backups/*"
    },
    {
      "Sid": "S3ListBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME",
      "Condition": {
        "StringLike": {
          "s3:prefix": "backups/*"
        }
      }
    }
  ]
}
```

Generate access keys for each user and record them in the appropriate place:

| User | Where credentials go |
|------|---------------------|
| `jw-django` | `.env` → `DJANGO_AWS_ACCESS_KEY_ID` / `DJANGO_AWS_SECRET_ACCESS_KEY` |
| `jw-planka` | `.env` → `PLANKA_S3_ACCESS_KEY_ID` / `PLANKA_S3_SECRET_ACCESS_KEY` |
| `jw-backup` | `/etc/restic/env` → `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |

---

## Step 3 — Amazon SES

### 3a. Verify your sending domain

1. Go to **SES → Verified identities → Create identity**.
2. Choose **Domain**, enter `yourdomain.com`.
3. Enable **Easy DKIM** — SES generates three CNAME records.
4. Add the CNAME records to your DNS.
5. Also add the provided TXT record for domain verification.
6. Wait for "Verification status: Verified" (usually a few minutes).

### 3b. Request production access (exit the sandbox)

New SES accounts are in the **sandbox** — they can only send to verified
addresses. You must request production access:

1. Go to **SES → Account dashboard → Request production access**.
2. Fill in the form; typical approval time is 24 hours.

### 3c. Create SMTP credentials

The backup notification emails (sent by msmtp from the VPS) use SMTP, not
the SES API. Create dedicated SMTP credentials:

1. Go to **SES → SMTP settings → Create SMTP credentials**.
2. Give the IAM user a name (e.g. `ses-smtp-user`).
3. Download the credentials file — **this is the only time the SMTP password
   is shown**.
4. Add to `/etc/restic/env`:
   ```
   SMTP_USER=<SMTP username from download>
   SMTP_PASSWORD=<SMTP password from download>
   SMTP_HOST=email-smtp.ap-southeast-2.amazonaws.com
   SMTP_PORT=587
   SMTP_TLS=on
   ```

> The Django app sends email via the SES **API** (boto3 / anymail), using the
> `jw-django` IAM credentials above — not SMTP. SMTP credentials are only
> needed for backup notifications from the VPS.

### 3d. Create a configuration set

The configuration set is named `TrackingConfigSet` in `settings/production.py`.

1. Go to **SES → Configuration sets → Create set**.
2. Name it exactly **`TrackingConfigSet`**.
3. Leave all other settings as defaults for now — you will add an SNS
   destination in the next step.

### 3e. Add DMARC and BIMI DNS records

Once SES domain verification and DKIM are passing, complete the mailbox-branding
setup by adding DMARC and BIMI records in DNS.

- DMARC is required by Gmail for bulk senders.
- BIMI enables supported inboxes to show the Journal Watch logo next to your mail.

The full record values, asset path, and verification checklist are documented in
[production-deploy.md](/Users/eamonnupperton/Documents/developer/spanza_journal_watch/docs/operations/production-deploy.md)
under:

- `Step 3b — Email authentication DNS records`
- `BIMI (brand logo in inbox)`

---

## Step 4 — SNS for bounce and complaint tracking

Django's anymail integration receives bounce and complaint events via an SNS
HTTP subscription, which Django then records for subscriber management.

### 4a. Create an SNS topic

1. Go to **SNS → Topics → Create topic**.
2. Type: **Standard**.
3. Name: `journalwatch-ses-events` (or similar).
4. Leave encryption, access policy, etc. as defaults.
5. Copy the **Topic ARN** — you need it in the next step.

### 4b. Add SNS as an SES configuration set destination

1. Go to **SES → Configuration sets → TrackingConfigSet → Event destinations**.
2. Click **Add destination**.
3. Event types: select **Bounce** and **Complaint** (and optionally Delivery).
4. Destination type: **Amazon SNS**.
5. Select the topic you just created.
6. Save.

### 4c. Subscribe Django to the SNS topic

Django's anymail SNS webhook listens at `/anymail/amazon_ses/tracking/`.

1. Go to **SNS → Topics → journalwatch-ses-events → Create subscription**.
2. Protocol: **HTTPS**.
3. Endpoint: `https://yourdomain.com/anymail/amazon_ses/tracking/`
4. Click **Create subscription** — SNS immediately sends a `SubscriptionConfirmation`
   request to the URL.
5. Django automatically confirms the subscription (anymail handles this).
   Check the subscription status — it should change to **Confirmed** within
   a few seconds once your app is running.

### 4d. Set the webhook secret

The `WEBHOOK_SECRET` in `.env` is used by anymail to verify that SNS
notifications are authentic. SNS signs each message with its own certificate —
anymail validates the signature automatically. The `WEBHOOK_SECRET` provides
an additional layer: SNS is configured to include it as a query parameter
in the callback URL.

Update the subscription endpoint to include the secret:

```
https://yourdomain.com/anymail/amazon_ses/tracking/?secret=YOUR-WEBHOOK-SECRET
```

Where `YOUR-WEBHOOK-SECRET` is the value of `WEBHOOK_SECRET` from your `.env`.

To update the subscription endpoint:
1. Delete the existing subscription.
2. Create a new one with the URL above.
3. Wait for Django to confirm it again.

### 4e. Configure inbound email (optional)

If you want to receive inbound email (e.g. for bounce handling via email):

1. Go to **SES → Email receiving → Create rule set** (if not already created).
2. Create a rule:
   - Recipient condition: `inbound@yourdomain.com` (or `*@yourdomain.com`)
   - Actions:
     1. **S3**: store in `YOUR-BUCKET-NAME`, prefix `email/`
     2. **SNS**: notify `journalwatch-ses-events` topic
3. Create an SNS subscription to `https://yourdomain.com/anymail/amazon_ses/inbound/`
   with the webhook secret as above.
4. Add an MX record to DNS:
   ```
   yourdomain.com  MX  10  inbound-smtp.ap-southeast-2.amazonaws.com
   ```

---

## Verification checklist

After completing setup, verify each component:

- [ ] S3 bucket: "Block all public access" enabled, versioning on
- [ ] IAM users: three separate users created with scoped policies
- [ ] `.env`: all three credential pairs populated (django, planka, restic)
- [ ] SES domain: "Verification status: Verified" in console
- [ ] SES: production access granted (out of sandbox)
- [ ] Configuration set `TrackingConfigSet` exists
- [ ] SNS topic has SES as event source for Bounce + Complaint
- [ ] SNS subscription to Django tracking webhook: "Confirmed"
- [ ] Send a test email from Django: `make shell` then
  ```python
  from django.core.mail import send_mail
  send_mail("Test", "Hello", None, ["you@example.com"])
  ```
- [ ] Trigger a test bounce (send to `bounce@simulator.amazonses.com`) and
  confirm Django receives the SNS notification (check logs)
