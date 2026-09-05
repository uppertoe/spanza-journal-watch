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

Production runs pre-built Docker Hub images on a single VPS behind the
hardened-Caddy scaffold. Nothing is built on the server.

| Image                                 | Purpose                                   |
| ------------------------------------- | ----------------------------------------- |
| `{namespace}/journalwatch-app:{tag}`  | Django under gunicorn, Celery worker      |
| `{namespace}/journalwatch-mjml:{tag}` | MJML TCP rendering server                 |
| `ghcr.io/plankanban/planka:2.1.0`     | Planka kanban (digest-pinned; SSO tested) |
| `postgres:17-alpine`                  | Django database                           |
| `postgres:16-alpine`                  | Planka database                           |
| `redis:7-alpine`                      | Celery broker, Django cache and sessions  |

Every push to `main` runs lint and the full test suite in GitHub Actions and,
only if they pass, builds both images and pushes them tagged `latest` and
`sha-<commit>`. A `v*` tag produces a versioned image instead. Base images and
the npm dependency trees are pinned, so a given commit always builds the same
image; Dependabot proposes the bumps.

The server repo (`journal-watch-vps`) is the runtime source of truth. The
files under [deploy/journalwatch](deploy/journalwatch) are a synced copy of its
`apps/journal-watch/` directory, kept here so application changes that need a
compose, Caddy or env change can be reviewed alongside them.

See **[docs/operations/production-deploy.md](docs/operations/production-deploy.md)** for the complete step-by-step deployment guide.

App-specific server export files live in [deploy/journalwatch](deploy/journalwatch). One-off bootstrap helpers live in [deploy/bootstrap](deploy/bootstrap).

---

## Code layout

Django apps live under `spanza_journal_watch/`: `submissions` (reviews, issues,
tags, the journal browser), `layout` (homepage, feeds, sitemaps), `newsletter`,
`analytics`, `cpd`, `events`, `users` and `backend`. Views are packages with one
module per page or workflow area and an `__init__.py` that re-exports every
view for the URL configuration: `submissions/views/` (reviews, issues, topics,
search, contributors, journal browser), `backend/views/` (intake, issue pages,
contributors, Planka, newsletter release, headlines, settings and so on) and
`backend/analytics_views/` (one module per analytics panel plus shared
helpers). A new view goes in its module and in that package's import block.
Styles are Sass partials under `static/sass/project/`, imported in order by
`project.scss`.

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

## Operations

Full runbooks are in `docs/operations/`, and deployment helper ownership is summarised in [deploy/README.md](/Users/eamonnupperton/Documents/developer/spanza_journal_watch/deploy/README.md):

- `production-deploy.md` — complete first-time deployment guide (start here)
- `aws-setup.md` — S3, IAM, SES, and SNS configuration reference
