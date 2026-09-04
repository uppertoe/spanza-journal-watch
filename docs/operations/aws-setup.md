# AWS setup — S3, SES, and SNS

Everything Journal Watch needs from AWS: three S3 buckets, three
least-privilege IAM users, an SES sending identity with tracking events, and
the SNS topics that feed Django's Anymail webhooks.

`deploy/bootstrap/aws_setup.py` is the source of truth for how these
resources are named and wired. **Read it before running any `aws` CLI
command that mutates SES, SNS or IAM** — ad-hoc changes to the event
destination have broken the webhook before. If a change does not fit the
script's pattern, change the script.

---

## Running the script

Run from this repo on your local machine with **admin** AWS credentials (not
the service keys that go into `.env`). It needs boto3; use a venv rather than
the system Python.

```bash
python3 -m venv /tmp/jw-ops-venv && source /tmp/jw-ops-venv/bin/activate
pip install boto3

python deploy/bootstrap/aws_setup.py \
  --profile <admin-profile> \
  --bucket <app-bucket> \
  --domain journalwatch.org.au \
  --webhook-secret "$(grep '^WEBHOOK_SECRET=' /path/to/apps/journal-watch/.env | cut -d= -f2-)" \
  --enable-inbound
```

The script is idempotent: existing resources are skipped or have their
policy re-applied; access keys are only created for users that have none.

| Flag | Default | Purpose |
|------|---------|---------|
| `--bucket` | required | App bucket (Django media and inbound email) |
| `--planka-bucket` | `<bucket>-planka` | Dedicated Planka bucket |
| `--backup-bucket` | `<bucket>-backups` | Dedicated Restic bucket |
| `--domain` | required | App domain, used in the SNS webhook URLs |
| `--ses-domain` | `--domain` | Domain for the SES identity; set to the production domain when reusing an already-verified identity (staging) |
| `--region` | `ap-southeast-2` | AWS region |
| `--profile` | default chain | Named profile from `~/.aws/config` |
| `--webhook-secret` | empty | `WEBHOOK_SECRET` from the app env, used only to print the ready-to-run `aws sns subscribe` commands |
| `--suffix` | empty | Appended to IAM user names, the SNS topics and the SES configuration set (`staging` → `jw-django-staging`, `TrackingConfigSet-staging`) |
| `--enable-inbound` | off | Also create the SES inbound receipt rule set and inbound SNS topic |
| `--backup-noncurrent-expiration-days` | `0` (off) | Lifecycle rule expiring noncurrent versions under `backups/` in the app bucket |

Unit tests for the script (they mock boto3 and need no credentials):

```bash
docker compose -f local.yml run --rm django pytest -q -o addopts='' deploy/bootstrap/tests/test_aws_setup.py
```

### What the script creates

| Resource | Details |
|----------|---------|
| App bucket | versioning on; SSE-S3; public ACLs blocked but bucket policies allowed; bucket policy granting public `s3:GetObject` on `media/*`; lifecycle rule aborting incomplete multipart uploads after 7 days |
| Planka bucket | versioning on; SSE-S3; no public read |
| Backup bucket | versioning **off**; SSE-S3; no public read |
| IAM `jw-django[-suffix]` | `media/*` read/write/delete, `email/*` read, `ListBucket` limited to those prefixes, `ses:SendEmail`/`ses:SendRawEmail`, `sns:ConfirmSubscription` |
| IAM `jw-planka[-suffix]` | full object access and `ListBucket` on the Planka bucket only |
| IAM `jw-backup[-suffix]` | full object access, `ListBucket` and `GetBucketLocation` on the backup bucket only |
| Access keys | one pair per newly created user, printed once |
| SES identity | `--ses-domain`, Easy DKIM with 2048-bit keys; DKIM CNAMEs printed (skipped when reusing an existing identity) |
| SES configuration set | `TrackingConfigSet[-suffix]` — matches `ANYMAIL_CONFIGURATION_SET_NAME` in the app env |
| SNS topic | `journalwatch-ses-events[-suffix]` |
| SES event destination | `TrackingToSNS[-suffix]` on the configuration set, sending `BOUNCE`, `COMPLAINT`, `DELIVERY_DELAY`, `REJECT`, `RENDERING_FAILURE` and `SUBSCRIPTION` to the topic |
| Inbound (with `--enable-inbound`) | SNS topic `journalwatch-ses-inbound[-suffix]`; receipt rule set `journalwatch-inbound[-suffix]` made active; catch-all rule `ReceiveToS3SNS[-suffix]` writing to the app bucket under `email/` and notifying the inbound topic; bucket policy statement allowing `ses.amazonaws.com` to put objects under `email/*` |

### What still needs a human

| Step | Why |
|------|-----|
| Add the printed DNS records (`_amazonses` TXT, three DKIM CNAMEs, MX if inbound) | your DNS provider |
| Request SES production access | AWS reviews manually, roughly 24 h |
| Run the printed `aws sns subscribe` command(s) | Django must be publicly reachable to confirm |
| Create SES SMTP credentials | only the SES console can mint them; used by the VPS backup alerts |

The script prints exact instructions for each.

---

## Bucket layout

```text
<app-bucket>/
├── media/          Django uploads (IAM jw-django; public read via bucket policy;
│                   proxied same-origin by Caddy at /media/*)
└── email/          inbound SES mail (written by SES, read by jw-django)

<planka-bucket>/    Planka attachments and backgrounds (IAM jw-planka)

<backup-bucket>/    Restic repositories, one per service (IAM jw-backup)
```

Static files are not in S3 — `collectstatic` writes them to the
`jw_staticfiles` volume and Caddy serves them.

The `media/*` public-read policy is what lets Caddy's `/media/*` proxy fetch
objects without signing requests; `AWS_QUERYSTRING_AUTH` is off in
`config/settings/production.py` for the same reason.

---

## Where the credentials go

| User | Destination |
|------|-------------|
| `jw-django` | `apps/journal-watch/.env` on the host → `DJANGO_AWS_ACCESS_KEY_ID`, `DJANGO_AWS_SECRET_ACCESS_KEY`, `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME`, `DJANGO_AWS_DEFAULT_REGION` |
| `jw-planka` | `apps/journal-watch/.env` → `PLANKA_S3_BUCKET`, `PLANKA_S3_ACCESS_KEY_ID`, `PLANKA_S3_SECRET_ACCESS_KEY`, `PLANKA_S3_REGION` |
| `jw-backup` | server repo `backup/config.env` → `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`; the bucket goes into each `backup/services/*.env` as `RESTIC_REPOSITORY=s3:s3.amazonaws.com/<backup-bucket>/<service>`; deployed to `/etc/restic/` by `ansible/backup.yml` |

The script's output labels the backup box `/etc/restic/env`; that is the
pre-scaffold path. The current scaffold reads `/etc/restic/config.env` and
`/etc/restic/services/<service>.env`, both written by Ansible from the server
repo — edit them there, not on the host.

After editing the app env on the host, recreate the services
(`docker compose up -d journal-watch jw_celeryworker jw_planka`), never
`restart`.

---

## SES

### Sending

The app sends through the SES API (`anymail.backends.amazon_ses.EmailBackend`)
with the `jw-django` keys, in `ap-southeast-2`, tagging every message with
the `TrackingConfigSet` configuration set. Nothing in the app uses SMTP.

New AWS accounts are sandboxed (verified recipients only) until production
access is granted from **SES → Account dashboard → Request production
access**.

### SMTP credentials (backup alerts only)

The VPS backup role sends failure/success mail via msmtp. Create credentials
at **SES → SMTP settings → Create SMTP credentials** (the password is shown
once) and put them in the server repo's `backup/config.env`:

```text
ALERT_EMAIL=you@example.com
SMTP_HOST=email-smtp.ap-southeast-2.amazonaws.com
SMTP_PORT=587
SMTP_TLS=on
SMTP_USER=<SMTP username>
SMTP_PASSWORD=<SMTP password>
SMTP_FROM=backup@journalwatch.org.au
```

Then `ansible-playbook -i ansible/hosts ansible/backup.yml` from the server
repo.

### DMARC and BIMI

Once the identity is verified and DKIM is passing, add the DMARC and BIMI
records described under "Email authentication DNS" in
[production-deploy.md](production-deploy.md).
`python manage.py check_email_auth` inside the app container reports SPF,
DKIM and DMARC status.

---

## SNS webhooks

Anymail receives SES events over HTTPS subscriptions. Both endpoints are
protected by HTTP basic auth using `WEBHOOK_SECRET` from the app env, which
must therefore be in `username:password` form. The secret is embedded in
the subscription URL as the userinfo part — not as a query parameter.

Tracking events (bounces, complaints, delivery delays, rejects, rendering
failures, subscription changes):

```bash
aws sns subscribe \
  --region ap-southeast-2 \
  --topic-arn arn:aws:sns:ap-southeast-2:<account-id>:journalwatch-ses-events \
  --protocol https \
  --notification-endpoint 'https://<WEBHOOK_SECRET>@journalwatch.org.au/anymail/amazon_ses/tracking/'
```

Inbound mail (only when `--enable-inbound` was used):

```bash
aws sns subscribe \
  --region ap-southeast-2 \
  --topic-arn arn:aws:sns:ap-southeast-2:<account-id>:journalwatch-ses-inbound \
  --protocol https \
  --notification-endpoint 'https://<WEBHOOK_SECRET>@journalwatch.org.au/anymail/amazon_ses/inbound/'
```

Django confirms the subscription automatically (the `jw-django` policy
includes `sns:ConfirmSubscription`); the SNS console should show
**Confirmed** within seconds. To rotate `WEBHOOK_SECRET`, change it in the
app env, recreate `journal-watch`, delete the subscription and create it
again with the new URL.

Inbound mail also needs an MX record: `journalwatch.org.au MX 10
inbound-smtp.ap-southeast-2.amazonaws.com`. The receipt rule is a catch-all
for the domain so auto-replies and mail to any address are captured; Django
reads the stored message from `email/` in the app bucket
(`DJANGO_ANYMAIL_INBOUND_S3_OBJECT_PREFIX=email`).

---

## Verification checklist

- [ ] App bucket: versioning on, public ACLs blocked, bucket policy grants read on `media/*` only
- [ ] Planka and backup buckets exist with no public access
- [ ] Three IAM users with the scoped inline policies (`aws iam get-user-policy --user-name jw-django --policy-name jw-django-policy`)
- [ ] `apps/journal-watch/.env` has the django and planka key pairs; `backup/config.env` has the backup pair
- [ ] SES identity verified; DKIM status "Successful"
- [ ] SES production access granted
- [ ] `TrackingConfigSet` has event destination `TrackingToSNS` → `journalwatch-ses-events`
- [ ] Tracking (and inbound, if enabled) subscriptions show **Confirmed**
- [ ] Send a test email from the app container:
  ```bash
  docker compose exec -T journal-watch python manage.py shell -c 'from django.core.mail import send_mail; send_mail("Test", "Hello", None, ["you@example.com"])'
  ```
- [ ] Send to `bounce@simulator.amazonses.com` and confirm the bounce appears in the app logs / subscriber status
- [ ] An image URL under `https://journalwatch.org.au/media/` returns 200
