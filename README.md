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

Production uses pre-built Docker Hub images — no build step is required on the server.

| Image                                 | Purpose                          |
| ------------------------------------- | -------------------------------- |
| `{namespace}/journalwatch-app:{tag}`  | Django app + Celery workers      |
| `{namespace}/journalwatch-mjml:{tag}` | MJML TCP rendering server        |
| `ghcr.io/plankanban/planka:2.1.0`     | Planka kanban (optional profile) |
| `postgres:17-alpine`                  | Django database                  |
| `postgres:16-alpine`                  | Planka database                  |
| `redis:7-alpine`                      | Celery broker                    |

| Profile   | Services added                     |
| --------- | ---------------------------------- |
| _(none)_  | Django, Postgres, Redis, MJML      |
| `workers` | Celery worker, Celery beat, Flower |
| `planka`  | Planka, Planka Postgres            |

See **[docs/operations/production-deploy.md](docs/operations/production-deploy.md)** for the complete step-by-step deployment guide.

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

- `production-deploy.md` — complete first-time deployment guide (start here)
- `aws-setup.md` — S3, IAM, SES, and SNS configuration reference
- `backup.rst` — backup architecture, configuration, local testing, restore procedures
