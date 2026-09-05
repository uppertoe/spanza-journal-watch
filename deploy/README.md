# Deploy Tooling

This repository contains two kinds of non-application files:

- `deploy/journalwatch/`
  A synced copy of `apps/journal-watch/` in the `journal-watch-vps` server
  repo (the runtime source of truth). Last synced from the server on
  2026-09-05. When a change here is needed in production, apply it in the
  server repo and copy it back so the two stay identical.
- `deploy/bootstrap/`
  One-off or occasional helper tools run from a local machine when creating or
  updating an environment.

## When To Use What

`deploy/journalwatch/`

- `docker-compose.yml`
  The app's services, pulled into the server's root compose file through
  `include:`. The Celery worker runs the beat scheduler in-process; there is
  no separate beat service, and Flower sits behind the unused `flower` profile.
- `deploy.sh`
  The per-app hook `~/deploy` runs on the host: starts Postgres, then runs
  `migrate` and `collectstatic` in a throw-away app container.
- `postgres/init/`
  Bootstraps Postgres query observability such as `pg_stat_statements` on
  fresh databases.
- `journalwatch.caddy`
  The app's Caddy site blocks (static files, `/media/*` proxy to S3, Planka).
- `.env.example`
  The per-app env template; `bootstrap/gen-env.sh` writes a populated copy.
- `planka/`
  The Planka terms pages and the login cover image mounted into the containers.

`deploy/bootstrap/`

- `gen-env.sh`
  Use to generate a starting `.env` locally for a new Journal Watch
  environment; it emits the same variable set the server compose file reads.
- `aws_setup.py`
  Use to provision the Journal Watch AWS resources from a local machine with
  AWS admin credentials.
- `backfill_inbox_threads.py`
  Use after restoring an older database dump to link legacy inbound emails into
  the newer threaded inbox model. Safe to rerun; start with `--dry-run`.
- `migrate_postgres.sh`
  Use for rare Postgres major-version or dump-import migrations.

## Not Owned Here Anymore

Backup and restore automation now belongs to the server repo / VPS layer, not
this application repo. The old backup scripts and systemd units were removed to
keep ownership clear.

## Existing Postgres Instances

The compose files now start Postgres with `pg_stat_statements`,
`track_io_timing=on`, and slow-query logging at `250ms`.

For an existing database volume, the init script will not rerun automatically.
After restarting Postgres with the new settings, run:

`docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"`
