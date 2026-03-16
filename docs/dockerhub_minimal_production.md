# Docker Hub deployment plan (minimal production)

This project can run from prebuilt images pushed to Docker Hub.

## Images to publish

- `journalwatch-app` (Django + gunicorn + static build + Celery entrypoints)
- `journalwatch-mjml` (MJML TCP server used by `django-mjml`)

A separate MJML image is recommended. There is no official image for this exact TCP server protocol/entrypoint in this project.

## 1) Build and push images

Use a namespace and tag (example values shown):

- `DOCKERHUB_NAMESPACE=yourdockerhubuser`
- `APP_TAG=2026.03.16`

Build:

- `docker build -f compose/production/django/Dockerfile -t yourdockerhubuser/journalwatch-app:2026.03.16 .`
- `docker build -f compose/production/mjml-tcp/Dockerfile -t yourdockerhubuser/journalwatch-mjml:2026.03.16 .`

Push:

- `docker push yourdockerhubuser/journalwatch-app:2026.03.16`
- `docker push yourdockerhubuser/journalwatch-mjml:2026.03.16`

## 2) Minimal production compose

Use:

- [compose/production/minimal.dockerhub.yml](compose/production/minimal.dockerhub.yml)

This file includes minimum required services:

- `django`
- `postgres`
- `redis`
- `mjml`

Optional workers are included behind profile `workers`:

- `celeryworker`
- `celerybeat`

## 3) Required environment files

Populate these on the server:

- `./.envs/.production/.django`
- `./.envs/.production/.postgres`

`django`/`celery` need at least DB + Redis + Django production settings and secrets.

## 4) Deploy commands

Set image variables (shell env or `.env` file in compose working directory):

- `export DOCKERHUB_NAMESPACE=yourdockerhubuser`
- `export APP_TAG=2026.03.16`

Start core services:

- `docker compose -f compose/production/minimal.dockerhub.yml pull`
- `docker compose -f compose/production/minimal.dockerhub.yml up -d`

Run migrations:

- `docker compose -f compose/production/minimal.dockerhub.yml run --rm django python manage.py migrate`

Start workers (recommended):

- `docker compose -f compose/production/minimal.dockerhub.yml --profile workers up -d`

## 5) Notes

- The compose file publishes Django on port `5000`; front with a reverse proxy / load balancer in real production.
- `collectstatic` is already called by `/start` in the Django image.
- If you need zero-downtime rolling deploys, pin immutable tags and switch `APP_TAG` per release.

## 6) GitHub Actions publishing to Docker Hub

Workflow file:

- [.github/workflows/dockerhub-images.yml](.github/workflows/dockerhub-images.yml)

Behavior:

- On push to `main`: builds and pushes both images with:
	- `sha-<shortsha>`
	- `latest`
- On push tag `v*` (for example `v2026.03.16`): pushes immutable version tags:
	- `2026.03.16`

Required GitHub secrets:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN` (Docker Hub access token with write permissions)

Optional GitHub variable:

- `DOCKERHUB_NAMESPACE` (if omitted, workflow uses `DOCKERHUB_USERNAME`)

### Secret management steps

1. In Docker Hub, create a personal access token.
2. In GitHub repository settings:
	 - **Settings → Secrets and variables → Actions → New repository secret**
	 - Add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
3. (Optional) add repository variable `DOCKERHUB_NAMESPACE` if pushing to an org namespace.
4. Restrict who can push to `main`/tags via branch and tag protection rules.
5. Rotate `DOCKERHUB_TOKEN` periodically and update the GitHub secret.
