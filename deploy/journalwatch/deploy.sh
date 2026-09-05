#!/usr/bin/env bash
set -euo pipefail

docker compose up -d jw_postgres
docker compose run --rm --no-deps journal-watch python manage.py migrate --noinput
docker compose run --rm --no-deps journal-watch python manage.py collectstatic --noinput
