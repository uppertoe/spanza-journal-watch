# Deploy Runbook

This runbook is the practical, command-heavy companion to
[production-deploy.md](production-deploy.md). It reflects the actual hurdles we
hit while bringing up staging in March 2026.

Use this when you want the exact commands for:

- logging into AWS locally
- provisioning AWS buckets and IAM users
- deploying on the VPS
- restoring PostgreSQL from SQL dumps
- restoring databases from Restic backups

## Assumptions

- Local repo path:
  `/Users/eamonnupperton/Documents/developer/spanza_journal_watch`
- VPS deploy root:
  `/opt/deploy`
- App directory inside the server repo:
  `/opt/deploy/apps/journal-watch`
- The server repo root is the Docker Compose project root.

## The Most Important Gotcha

Use `/opt/deploy` as the Docker Compose working directory for the live stack.

Do not switch back and forth between:

- `/opt/deploy`
- `/opt/deploy/apps/journal-watch`

for the same running services.

We hit a real issue where:

- the restore had been run under the `/opt/deploy` project
- later checks were run inside `/opt/deploy/apps/journal-watch`
- Docker Compose then treated them as different projects
- that caused misleading container names and a `127.0.0.1:5432` port conflict

If in doubt, verify the active stack from `/opt/deploy`:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose ps
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

## 1. Local AWS Login

Optional, but helpful if you use AWS SSO or multiple profiles.

List available profiles:

```bash
aws configure list-profiles
```

If you use AWS SSO:

```bash
aws sso login --profile <your-profile>
```

If you use long-lived credentials in the default profile, you can usually omit
`--profile` from `aws_setup.py`.

The error:

```text
botocore.exceptions.ProfileNotFound: The config profile (...) could not be found
```

means the named profile does not exist locally. Use `aws configure list-profiles`
first instead of guessing.

## 2. Provision AWS Resources

Run from the repo root on your local machine:

```bash
cd /Users/eamonnupperton/Documents/developer/spanza_journal_watch
python ops/aws_setup.py \
  --profile default \
  --bucket spanza-journal-watch-staging-150064991851 \
  --planka-bucket spanza-journal-watch-staging-150064991851-planka \
  --domain staging.journalwatch.org.au \
  --ses-domain journalwatch.org.au \
  --suffix staging
```

For production, use the production bucket names and omit `--suffix` if you want
the unsuffixed IAM user names:

```bash
cd /Users/eamonnupperton/Documents/developer/spanza_journal_watch
python ops/aws_setup.py \
  --profile default \
  --bucket spanza-journal-watch-production-150064991851 \
  --planka-bucket spanza-journal-watch-production-150064991851-planka \
  --domain journalwatch.org.au
```

Notes:

- Django stays on the main app bucket.
- Planka now has its own bucket via `PLANKA_S3_BUCKET`.
- If IAM users already exist, the script updates their policies and reuses the
  existing access keys.

## 3. Update the VPS `.env`

On the VPS:

```bash
cd /opt/deploy/apps/journal-watch
nano .env
```

Make sure these are set correctly:

```dotenv
DJANGO_AWS_STORAGE_BUCKET_NAME=spanza-journal-watch-staging-150064991851
PLANKA_S3_BUCKET=spanza-journal-watch-staging-150064991851-planka
PLANKA_S3_ACCESS_KEY_ID=...
PLANKA_S3_SECRET_ACCESS_KEY=...
PLANKA_S3_REGION=ap-southeast-2
```

## 4. Pull and Deploy on the VPS

Use the server repo root, not the app subdirectory:

```bash
cd /opt/deploy
git pull
set -a; source apps/journal-watch/.env; set +a
docker compose --profile workers --profile planka pull
docker compose --profile workers --profile planka up -d
```

Useful verification commands:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose ps
docker compose logs --tail=100 jw_django jw_planka jw_celeryworker jw_celerybeat
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

For a narrower Planka restart after changing only bucket config:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose --profile planka up -d jw_planka jw_planka_postgres
```

## 5. Bootstrap / OIDC / Planka Setup Commands

From `/opt/deploy`:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose exec -T jw_django python manage.py migrate
docker compose exec -T jw_django python manage.py setup_planka_oidc
```

If needed, inspect the generated OAuth app:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose exec -T jw_django \
  python manage.py shell -c "
from oauth2_provider.models import Application
print(list(Application.objects.values('name', 'client_id', 'redirect_uris')))
"
```

## 6. PostgreSQL Restore From a SQL Dump

This is for restoring from a `.sql` or `.sql.gz` dump rather than from Restic.

Run the migration script from the server repo root so the Compose project stays
consistent:

```bash
cd /opt/deploy
./ops/migrate_postgres.sh \
  --compose-file apps/journal-watch/docker-compose.yml \
  --env-file apps/journal-watch/.env \
  --service jw_postgres \
  --force \
  /path/to/backup.sql.gz
```

What this script does:

- snapshots the existing Docker volume first
- removes the old PostgreSQL data volume
- starts a fresh Postgres 17 volume
- imports the sanitized SQL dump

After restore, verify row counts from `/opt/deploy`, not the app directory:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose exec -T jw_postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    select
      (select count(*) from users_user) as users,
      (select count(*) from submissions_article) as articles,
      (select count(*) from submissions_review) as reviews,
      (select count(*) from submissions_issue) as issues,
      (select count(*) from newsletter_subscriber) as subscribers,
      (select count(*) from layout_homepage) as homepages;
  "
```

If the homepage still fails after a restore, check whether a homepage is marked
current:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose exec -T jw_postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    select id, issue_id, publication_ready, created
    from layout_homepage
    order by created desc;
  "
```

## 7. Restoring From Restic Backups

This is separate from `migrate_postgres.sh`. Use this when you want to restore
from the Restic repository in S3.

List snapshots:

```bash
sudo bash -c 'source /etc/restic/env && restic snapshots'
```

Safe restore to a temporary Django database:

```bash
sudo bash -c 'source /etc/restic/env && /opt/backup/restore.sh --database django --target django_restored'
```

Safe restore to a temporary Planka database:

```bash
sudo bash -c 'source /etc/restic/env && /opt/backup/restore.sh --database planka --target planka_restored'
```

Inspect the restored Django database:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
psql -h 127.0.0.1 -U "$POSTGRES_USER" -d django_restored
```

Inspect the restored Planka database:

```bash
psql -h 127.0.0.1 -U postgres -d planka_restored
```

Restore from a specific snapshot:

```bash
sudo bash -c 'source /etc/restic/env && /opt/backup/restore.sh --database django --snapshot abc1234 --target django_restored'
```

Destructive live restore for Django, only as a last resort:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose stop jw_django jw_celeryworker jw_celerybeat
sudo bash -c 'source /etc/restic/env && /opt/backup/restore.sh --database django'
docker compose start jw_django jw_celeryworker jw_celerybeat
```

## 8. Fast Checks After Deploy or Restore

Health and container state:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose ps
docker compose logs --tail=50 jw_django jw_planka
```

Check static asset cache headers after deploy:

```bash
curl -I https://staging.journalwatch.org.au/static/webpack_bundles/css/project.716033be732d2928a3e7.css
curl -I https://staging.journalwatch.org.au/static/js/color-modes.js
curl -I https://staging.journalwatch.org.au/
```

Expected:

- hashed webpack assets should get long-lived immutable cache headers
- HTML compression depends on the reverse proxy config, not Django alone

Check Planka bucket usage after the dedicated bucket split:

```bash
cd /opt/deploy
set -a; source apps/journal-watch/.env; set +a
docker compose logs --tail=100 jw_planka
```

The old error looked like:

```text
AccessDenied ... s3:PutObject ... /protected/background-images/...
```

With `PLANKA_S3_BUCKET` configured, Planka should stop writing into the Django
bucket altogether.

## 9. Common Hurdles We Actually Hit

### Compose project mismatch

Symptom:

```text
network with name jw_internal exists but was not created for project "journal-watch"
Bind for 127.0.0.1:5432 failed: port is already allocated
```

Cause:

- some commands were run from `/opt/deploy`
- later commands were run from `/opt/deploy/apps/journal-watch`

Fix:

- standardize on `/opt/deploy` for all live stack commands

### Wrong AWS profile name

Symptom:

```text
botocore.exceptions.ProfileNotFound
```

Fix:

```bash
aws configure list-profiles
aws sso login --profile <real-profile>
```

or use `--profile default` if that is the configured admin profile.

### Planka writing into the Django bucket

Symptom:

```text
AccessDenied ... s3:PutObject on .../protected/background-images/...
```

Cause:

- Planka uses prefixes beyond `attachments/*`
- a shared bucket made IAM brittle

Fix:

- dedicate a bucket to Planka
- set `PLANKA_S3_BUCKET`
- redeploy Planka

### Restored DB looked empty or wrong

Cause:

- checks were run against the wrong Compose project / container

Fix:

- verify from `/opt/deploy`
- use `docker compose ps`
- inspect actual running container names with `docker ps`

## 10. Suggested Deployment Order

For a normal application deploy:

1. update AWS config first if bucket or IAM changes are involved
2. update `.env` on the VPS
3. `git pull` in `/opt/deploy`
4. `docker compose --profile workers --profile planka pull`
5. `docker compose --profile workers --profile planka up -d`
6. run `migrate` and any one-off setup commands
7. verify logs, homepage, Planka SSO, and backup visibility
