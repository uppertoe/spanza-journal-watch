# Production deployment

This guide covers a complete first-time deployment, from an empty VPS to a fully operational Journal Watch instance. Images are pulled from Docker Hub — no build step is needed on the server.

---

## Prerequisites

### VPS

Any Linux VPS running a recent Debian/Ubuntu release works. Minimum recommended spec:

| Resource | Minimum | Notes |
|----------|---------|-------|
| CPU | 2 vCPU | Celery workers are I/O-bound |
| RAM | 2 GB | Planka adds ~400 MB |
| Disk | 20 GB | Logs, database, Docker layers |

### Software on the VPS

```bash
# Docker Engine (not Docker Desktop)
curl -fsSL https://get.docker.com | sh

# Docker Compose v2 is included with Docker Engine (docker compose, not docker-compose)
docker compose version
```

### DNS

Create these records before deploying (propagation can take a few minutes):

| Type | Name | Value |
|------|------|-------|
| A | `yourdomain.com` | VPS IP |
| A | `planka.yourdomain.com` | VPS IP |

SES DKIM records are added later after running `aws_setup.py`. The DMARC and BIMI records are added after SES is configured — see Step 3b below.

### AWS account

You need an AWS account with admin credentials to provision the infrastructure. The infrastructure credentials (`jw-django`, `jw-planka`, `jw-backup`) are separate least-privilege users created by the setup script.

### Docker Hub access

The images are built and pushed by the GitHub Actions workflow. Confirm the images are available:

```bash
docker pull your-namespace/journalwatch-app:latest
docker pull your-namespace/journalwatch-mjml:latest
```

---

## Overview: what runs where

Most setup work happens on your **local machine** — the VPS only needs Docker and a small set of config files.

| Task | Where |
|------|-------|
| Generate `.env` (`gen-env.sh`) | Local machine |
| Provision AWS (`aws_setup.py`) | Local machine (needs admin AWS credentials) |
| Compose file, Makefile, Planka terms | VPS (copied or cloned) |
| Backup install scripts | VPS |
| Application images | Pulled from Docker Hub by the VPS |

---

## Step 1 — Get the config files onto the VPS

The VPS needs the Compose file, Makefile, Planka terms content, and backup scripts. The easiest way is to clone the repository — not to build anything, but to have these config files in a known location that `git pull` can update when they change.

```bash
git clone https://github.com/your-org/spanza-journal-watch.git /opt/journalwatch
cd /opt/journalwatch
```

If you prefer not to keep git on the server, you can `scp` just the files that are needed:

```
compose.prod.example.yml
Makefile
planka/custom/          (Planka terms content — bind-mounted into the container)
ops/systemd/            (backup install scripts)
```

Everything else in the repository (`ops/gen-env.sh`, `ops/aws_setup.py`, application source, etc.) stays on your local machine.

---

## Step 2 — Generate the environment file (local machine)

Run this on your local machine, not the VPS:

```bash
bash ops/gen-env.sh
```

The script prompts for four values:

| Prompt | Example |
|--------|---------|
| Primary domain | `journalwatch.org.au` |
| Planka subdomain | `planka.journalwatch.org.au` _(default: `planka.<domain>`)_ |
| Admin email | `admin@journalwatch.org.au` |
| Docker Hub namespace | `your-namespace` |

Everything else (Django secret key, database password, OIDC RSA key, Flower credentials, etc.) is generated automatically. The script writes `.env` (mode `600`) and a pre-filled `ops/systemd/env.local` for the backup configuration.

After the script completes, there are five AWS placeholders to fill in — these come from Step 3:

```
DJANGO_AWS_ACCESS_KEY_ID=REPLACE_WITH_IAM_ACCESS_KEY
DJANGO_AWS_SECRET_ACCESS_KEY=REPLACE_WITH_IAM_SECRET_KEY
DJANGO_AWS_STORAGE_BUCKET_NAME=REPLACE_WITH_BUCKET_NAME
PLANKA_S3_ACCESS_KEY_ID=REPLACE_WITH_PLANKA_IAM_ACCESS_KEY
PLANKA_S3_SECRET_ACCESS_KEY=REPLACE_WITH_PLANKA_IAM_SECRET_KEY
```

---

## Step 3 — Provision AWS infrastructure (local machine)

Run with **admin** AWS credentials on your local machine — not the VPS, and not the service credentials that will go into `.env`.

The script needs boto3. Use a venv rather than your system Python:

```bash
python3 -m venv /tmp/jw-ops-venv
source /tmp/jw-ops-venv/bin/activate
pip install boto3

python ops/aws_setup.py \
  --bucket your-bucket-name \
  --domain yourdomain.com \
  --webhook-secret "$(grep WEBHOOK_SECRET .env | cut -d= -f2)"
```

The script creates:

- S3 bucket with versioning, block-public-access, SSE-S3, and lifecycle rules
- IAM users `jw-django` (media + SES), `jw-planka` (attachments only), `jw-backup` (backups only) with scoped inline policies
- SES email identity for your domain and the `TrackingConfigSet` configuration set
- SNS topic `journalwatch-ses-events` wired to SES Bounce + Complaint events

It prints:

- **IAM access keys** — paste the `jw-django` and `jw-planka` keys into `.env`, and the `jw-backup` keys into `ops/systemd/env.local`
- **DNS records** — add the SES TXT and three DKIM CNAMEs to your DNS provider
- **Manual steps** that require human action (SES production access, SNS webhook subscription)

Once `.env` is complete, copy it to the VPS:

```bash
scp .env user@your-vps:/opt/journalwatch/.env
```

> **SES sandbox**: new AWS accounts are in the sandbox and can only send to verified addresses. Request production access at the URL the script prints. Approval typically takes 24 hours.

See [aws-setup.md](aws-setup.md) for the full AWS configuration reference and IAM policy details.

---

## Step 3b — Email authentication DNS records

After `aws_setup.py` prints the SES DNS records and you have confirmed the domain identity is verified, add the following additional records to complete the email authentication chain.

### DMARC

DMARC is required by Gmail for bulk senders. Start with `p=none` (reporting only) until you have confirmed SPF and DKIM are both passing cleanly, then move to `p=quarantine`.

| Type | Name | Value |
|------|------|-------|
| TXT | `_dmarc.yourdomain.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@yourdomain.com; pct=100` |

Verify all three pass before moving on:

```bash
# Check from any machine
dig TXT _dmarc.yourdomain.com
# Send a test newsletter to mail-tester.com and confirm dmarc=pass in the headers
```

### BIMI (brand logo in inbox)

BIMI causes Gmail, Apple Mail, Yahoo, and Fastmail to display the Journal Watch newsstand icon next to your emails in the inbox. It requires DMARC with `p=quarantine` or `p=reject` to be passing first.

The SVG is already in the repository at `spanza_journal_watch/static/images/logo/newsstand-icon-bimi.svg` in the required **SVG Tiny PS** format. Once `collectstatic` has run in production it is publicly accessible at:

```
https://yourdomain.com/static/images/logo/newsstand-icon-bimi.svg
```

Add the BIMI DNS record:

| Type | Name | Value |
|------|------|-------|
| TXT | `default._bimi.yourdomain.com` | `v=BIMI1; l=https://yourdomain.com/static/images/logo/newsstand-icon-bimi.svg;` |

Verify the record is live:

```bash
dig TXT default._bimi.yourdomain.com
```

Gmail may take a few days to pick up the logo after the record propagates. Apple Mail and Fastmail typically apply it within hours. A **Verified Mark Certificate (VMC)** from DigiCert or Entrust would add a blue verified checkmark in Gmail but costs ~$1,500/yr and is not required for the logo to display.

> **Checklist before adding BIMI:**
> - [ ] SPF passes (`dig TXT yourdomain.com` includes `include:amazonses.com`)
> - [ ] DKIM passes (visible in email headers as `dkim=pass`)
> - [ ] DMARC is `p=quarantine` or `p=reject` and passing
> - [ ] `collectstatic` has run and the SVG is reachable at the URL above

---

## Step 4 — Configure the reverse proxy (VPS)

The app expects TLS to be terminated upstream. Both services expose plain HTTP internally:

| Service | Internal port | Exposes |
|---------|--------------|---------|
| Django | 5000 | Main app |
| Planka | 3001 | Kanban board |

A minimal nginx configuration:

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 443 ssl;
    server_name planka.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/planka.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/planka.yourdomain.com/privkey.pem;

    # Planka uses WebSockets for real-time updates
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`DJANGO_SECURE_SSL_REDIRECT=False` is set in `.env` by default because TLS is terminated at the proxy, not Django.

---

## Step 5 — Pull images and start the stack (VPS)

From `/opt/journalwatch` on the VPS:

```bash
make pull    # pulls all images (django, mjml, planka, postgres, redis)
make up      # starts the full stack with all profiles
```

Verify all containers are healthy:

```bash
make ps
```

All services should show `running` or `healthy` within about 30 seconds.

---

## Step 6 — Bootstrap the application

Run once after the stack is up for the first time:

```bash
make bootstrap
```

This runs in order:

1. `migrate` — applies all database migrations
2. `create_chief_editor` — creates the chief editor account (prompts for email and password)
3. `setup_planka_oidc` — registers Django as the Planka OIDC provider

---

## Step 7 — Set up the Planka API key

1. Open `https://yourdomain.com/backend/settings/` in your browser and log in as the chief editor.
2. Click **Set up Planka API key**. This authenticates to Planka using the bootstrap credentials and stores an API token for all subsequent Planka operations.

After this succeeds:

3. Uncomment `OIDC_ENFORCED=true` in `.env` (it is already present but commented out).
4. Restart the stack to pick up the change:

```bash
make restart
```

From this point, Planka login is SSO-only — users log in through Django. The `DEFAULT_ADMIN_PASSWORD` in `.env` is no longer used and can be removed.

---

## Step 8 — Subscribe SNS to the Django webhook

Once Django is publicly reachable, run the `aws sns subscribe` command that `aws_setup.py` printed in Step 3. It looks like:

```bash
aws sns subscribe \
  --region ap-southeast-2 \
  --topic-arn arn:aws:sns:ap-southeast-2:123456789012:journalwatch-ses-events \
  --protocol https \
  --notification-endpoint 'https://yourdomain.com/anymail/amazon_ses/tracking/?secret=YOUR-WEBHOOK-SECRET'
```

Django auto-confirms the subscription. Check the SNS console — the subscription status should change to **Confirmed** within a few seconds.

---

## Step 9 — Set up backups

First, copy the pre-filled backup env file from your local machine to the VPS:

```bash
# On your local machine
scp ops/systemd/env.local user@your-vps:/tmp/restic-env
```

Then on the VPS:

```bash
# Install restic, msmtp, and the backup scripts/systemd units
sudo bash ops/systemd/install.sh

# Put the pre-filled env in place
sudo cp /tmp/restic-env /etc/restic/env
sudo chmod 600 /etc/restic/env
rm /tmp/restic-env

# Fill in the IAM backup credentials and SES SMTP credentials
sudo nano /etc/restic/env
```

The values to fill in:

| Key | Where to get it |
|-----|----------------|
| `RESTIC_REPOSITORY` | Update the bucket name printed by `aws_setup.py` |
| `AWS_ACCESS_KEY_ID` | `jw-backup` key printed by `aws_setup.py` |
| `AWS_SECRET_ACCESS_KEY` | `jw-backup` secret printed by `aws_setup.py` |
| `SMTP_USER` / `SMTP_PASSWORD` | SES SMTP credentials (AWS console → SES → SMTP settings) |

Initialise the repository and verify:

```bash
# Initialise the Restic repository (first time only)
sudo bash -c 'source /etc/restic/env && restic init'

# Start the timer
sudo systemctl start backup.service
sudo journalctl -u backup.service -f   # watch the first backup complete

# Confirm the daily timer is scheduled
sudo systemctl list-timers backup.timer
```

See [backup.rst](backup.rst) for the full backup runbook, retention policy, and restore procedures.

---

## Verification checklist

After completing all steps, verify the following:

- [ ] `make ps` — all containers `running` or `healthy`
- [ ] `https://yourdomain.com/` — app loads
- [ ] `https://yourdomain.com/backend/` — backend login works
- [ ] `https://planka.yourdomain.com/` — redirects to Django login (OIDC enforced)
- [ ] Log in to Planka via SSO
- [ ] Create a test newsletter issue — confirm a Planka board is created
- [ ] Send a test email from the backend — confirm delivery via SES
- [ ] Send a newsletter test to [mail-tester.com](https://www.mail-tester.com) — confirm SPF, DKIM, and DMARC all pass
- [ ] Trigger a test bounce (`bounce@simulator.amazonses.com`) — confirm SNS webhook fires and Django logs it
- [ ] Confirm BIMI logo appears in Gmail and Apple Mail (may take up to 48 hours after DNS propagation)
- [ ] Check `systemctl list-timers backup.timer` — next backup scheduled
- [ ] Run a manual backup restore to a test database (see backup runbook)

---

## Ongoing operations

### Deploying a new version

```bash
make pull      # pull new images from Docker Hub
make restart   # recreate containers with the new images
make migrate   # apply any new migrations (safe to run every time)
```

### Useful commands

```bash
make logs      # tail all container logs
make ps        # container status
make shell     # Django management shell
make migrate   # run migrations
```

### Updating `.env`

After any change to `.env`:

```bash
make restart
```

### Scaling workers

Edit `WEB_CONCURRENCY` in `.env` (Gunicorn workers) and `make restart`. Celery concurrency is set in the Django settings; adjust `CELERY_WORKER_CONCURRENCY` if needed.
