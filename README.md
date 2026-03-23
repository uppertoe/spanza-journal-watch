# SPANZA Journal Watch

A web application for the [Society of Paediatric Anaesthesia in New Zealand and Australia](https://spanza.org.au/) that manages the editorial workflow for the SPANZA Journal Watch newsletter.

---

## Features

### Article intake and review

- Fetches articles automatically from PubMed on a configurable schedule
- Reviewers are notified by email and complete structured reviews (summary, commentary, star rating) via a web form
- Chief editor and regional coordinators assign articles and monitor review progress

### Issue builder

- Assemble reviewed articles into a newsletter issue with drag-and-drop ordering
- Review editor allows inline editing of all review fields before publication
- Issue workflow tracks status from draft → coordinator review → ready to publish

### Newsletter release

- Issues are rendered to HTML email via MJML and dispatched through Amazon SES
- Subscriber management with double opt-in via allauth

### Planka kanban integration

- Each newsletter issue is mirrored as a Planka board for editorial tracking
- Articles appear as cards; reviewers are added as board members
- Planka is provisioned automatically via the Django admin and synced through the issue lifecycle

### Access control

- Role-based: **chief editor**, **regional coordinator**, **reviewer**
- Django acts as an OIDC provider — Planka uses SSO, no separate login required
- Invite-based onboarding for reviewers and coordinators

---

## Deployment

### Overview

Production uses pre-built Docker Hub images. No build step is required on the server.

| Image                                 | Purpose                          |
| ------------------------------------- | -------------------------------- |
| `{namespace}/journalwatch-app:{tag}`  | Django app + Celery workers      |
| `{namespace}/journalwatch-mjml:{tag}` | MJML TCP rendering server        |
| `ghcr.io/plankanban/planka:2.0.3`     | Planka kanban (optional profile) |
| `postgres:17-alpine`                  | Django database                  |
| `postgres:16-alpine`                  | Planka database                  |
| `redis:7-alpine`                      | Celery broker                    |

### Compose profiles

| Profile   | Services added                     |
| --------- | ---------------------------------- |
| _(none)_  | Django, Postgres, Redis, MJML      |
| `workers` | Celery worker, Celery beat, Flower |
| `planka`  | Planka, Planka Postgres            |

### Quick start

```bash
# 1. Copy and edit the environment file
cp .env.example .env
$EDITOR .env

# 2. Start the core stack (no Celery workers, no Planka)
docker compose -f compose.prod.example.yml up -d

# 3. Start everything
docker compose -f compose.prod.example.yml --profile workers --profile planka up -d
```

### Reverse proxy

The app listens on port `5000`. Planka listens on port `3001`. Both should be placed behind a TLS-terminating reverse proxy (nginx, Caddy, Traefik, AWS ALB, etc.) — this is not included in the compose file.

Set `DJANGO_SECURE_SSL_REDIRECT=False` when TLS is terminated upstream.

### Backups

Databases are backed up with [restic](https://restic.net/) to S3 via a VPS-level systemd timer (independent of the Docker stack). Setup files are in `ops/systemd/`.

```bash
# Install and enable the backup timer on the VPS
sudo bash ops/systemd/install.sh
```

See `docs/operations/backup.rst` for the full runbook, including restore procedures.

---

## Important setup

### 1. Generate secrets

Several values in `.env` must be generated before first boot:

```bash
# Django secret key
python -c "import secrets; print(secrets.token_urlsafe(64))"

# Postgres password, Flower credentials, OIDC client secret
python -c "import secrets; print(secrets.token_urlsafe(24))"

# Planka SECRET_KEY and OIDC client secret
openssl rand -hex 32

# OIDC RSA private key (base64-encoded, for Django OIDC provider)
openssl genrsa 4096 | base64 -w 0
```

### 2. Run migrations and create the first superuser

```bash
docker compose -f compose.prod.example.yml exec django python manage.py migrate
docker compose -f compose.prod.example.yml exec django python manage.py createsuperuser
```

### 3. Create the chief editor account

```bash
docker compose -f compose.prod.example.yml exec django \
  python manage.py create_chief_editor
```

### 4. Set up Planka OIDC (SSO)

Run once after first boot. This registers Django as the OIDC provider in the database and sets `OIDC_CLIENT_SECRET` in Planka.

```bash
docker compose -f compose.prod.example.yml --profile planka exec django \
  python manage.py setup_planka_oidc
```

### 5. Set up the Planka API key

Open the Django backend settings page (`/backend/settings/`) and click **Set up Planka API key**. This performs the initial Planka admin login and stores an API token that Django uses for all subsequent Planka operations.

After this step completes, you can enable `OIDC_ENFORCED=true` in `.env` and restart Planka to enforce SSO.

### 6. Amazon SES

- Verify your sending domain in SES.
- Create SMTP credentials and add them to your anymail configuration.
- Set up an SNS webhook for bounce/complaint handling and record the signing secret as `WEBHOOK_SECRET`.

---

## Local development

`.env.local` is tracked in git and contains ready-to-use dev credentials — no setup needed.

```bash
# Start the full local stack (Django, Postgres, Redis, MJML, Planka, Celery)
docker compose -f local.yml --profile planka --profile workers up

# Django shell
docker exec spanza_journal_watch_local_django /entrypoint python manage.py shell

# Emails are caught by Mailpit at http://localhost:8025
# Planka is at http://localhost:3001
# Flower (Celery monitor) is at http://localhost:5555
```

### Test the backup scripts locally

```bash
# Dry run
docker compose -f local.yml --profile backup run --rm backup /backup/backup.sh --dry-run

# Full backup + verify
docker compose -f local.yml --profile backup run --rm backup /backup/backup.sh --verify

# Test restore into a temporary database (safe — does not touch the real DB)
docker compose -f local.yml --profile backup run --rm backup \
  /backup/restore.sh --database django --target django_restore_test
```

---

## Operations

Full runbooks are in `docs/operations/`:

- `backup.rst` — backup architecture, configuration, local testing, restore procedures
