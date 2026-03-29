# Deploy Tooling

This repository contains two kinds of non-application files:

- `deploy/journalwatch/`
  App-specific files that are copied into `apps/journalwatch/` in the
  `server-instance-template` server repo.
- `deploy/bootstrap/`
  One-off or occasional helper tools run from a local machine when creating or
  updating an environment.

## When To Use What

`deploy/journalwatch/`

- `docker-compose.yml`
  Use when wiring Journal Watch into the server repo under `apps/journalwatch/`.
- `journalwatch.caddy`
  Use when exposing the app through the server repo's Caddy setup.
- `.env.example`
  Use as the per-app env template in the server repo.
- `planka/custom/`
  Use for the Planka terms pages mounted into the Planka container.

`deploy/bootstrap/`

- `gen-env.sh`
  Use to generate a starting `.env` locally for a new Journal Watch
  environment.
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
