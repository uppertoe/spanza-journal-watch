# Deploy runbook

The command-by-command companion to
[production-deploy.md](production-deploy.md). Everything here is what a
maintainer actually types, in order, with the paths on the host. It also
records the hurdles we have hit so nobody has to rediscover them.

## Conventions used below

- Host: SSH alias `journal-watch-vps`.
- Compose project root on the host: `/opt/deploy`. Every `docker compose`
  command below assumes `cd /opt/deploy` first.
- Django service: `journal-watch`. Worker: `jw_celeryworker`. Databases:
  `jw_postgres` (Django) and `jw_planka_postgres` (Planka). Board: `jw_planka`.
- App env file: `/opt/deploy/apps/journal-watch/.env`.
- Local clone of the server repo:
  `~/Documents/developer/journal-watch-vps`.
- Local clone of this repo:
  `~/Documents/developer/spanza_journal_watch`.

## The most important gotcha

Run the live stack **only** from `/opt/deploy`. Running `docker compose` from
`/opt/deploy/apps/journal-watch` creates a second Compose project
(`journal-watch` instead of `deploy`) with its own container and volume
names. Symptoms:

```text
network with name jw_internal exists but was not created for project "journal-watch"
Bind for 127.0.0.1:5432 failed: port is already allocated
```

If in doubt:

```bash
cd /opt/deploy
docker compose ps
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

You do not need to `source` the app env file: the root `docker-compose.yml`
pulls the app file in via `include:`, so Compose interpolates it against
`apps/journal-watch/.env` on its own.

---

## 1. Routine application deploy

```bash
# local machine — after merging to main
gh run list --workflow dockerhub-images.yml --limit 1
gh run watch <run-id>                 # ~2 min; builds uppertoe/journalwatch-app:latest

ssh journal-watch-vps ./deploy        # git pull, compose pull, migrate, collectstatic, up, prune
```

Check:

```bash
ssh journal-watch-vps 'cd /opt/deploy && docker compose ps'
ssh journal-watch-vps 'cd /opt/deploy && docker compose logs --tail=50 journal-watch jw_celeryworker'
curl -sS -o /dev/null -w '%{http_code}\n' https://journalwatch.org.au/healthz
```

Do not run `migrate` or `collectstatic` by hand — the
`apps/journal-watch/deploy.sh` hook does both on every `./deploy`.

## 2. Roll back to a previous image

Every `main` build is also tagged `sha-<7-char commit>` on Docker Hub.

```bash
ssh journal-watch-vps
cd /opt/deploy
$EDITOR apps/journal-watch/.env        # JW_APP_TAG=sha-abc1234
./deploy
```

Restore `JW_APP_TAG=latest` and run `./deploy` again to resume tracking
`main`. Migrations are not rolled back automatically; check
`python manage.py showmigrations` if the release you are backing out added
one.

## 3. Change an environment variable

```bash
ssh journal-watch-vps
cd /opt/deploy
$EDITOR apps/journal-watch/.env
docker compose up -d journal-watch jw_celeryworker      # recreate; add jw_planka if it reads the var
docker compose exec -T journal-watch env | grep DJANGO_AWS_S3_CUSTOM_DOMAIN
```

`docker compose restart` does **not** pick up env-file edits. On 2026-07-13
`DJANGO_AWS_S3_CUSTOM_DOMAIN` was on disk but absent from the running
container for exactly this reason.

## 4. Logs, shell, one-off commands

```bash
cd /opt/deploy
docker compose logs -f --tail=200 journal-watch jw_celeryworker jw_planka
docker compose exec -T journal-watch python manage.py shell -c 'from spanza_journal_watch.users.models import User; print(User.objects.count())'
docker compose exec -T journal-watch python manage.py <command>          # see management-commands.md
docker compose exec -T jw_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

From your laptop in one line:

```bash
ssh journal-watch-vps "cd /opt/deploy && docker compose exec -T journal-watch python manage.py shell -c '...'"
```

Caddy's JSON access log is on the `caddy_logs` volume at
`/var/log/caddy/access.log` inside the container:

```bash
docker compose exec -T caddy tail -n 100 /var/log/caddy/access.log
```

## 5. Change Caddy routes or add a service

In the local clone of the server repo:

```bash
cd ~/Documents/developer/journal-watch-vps
$EDITOR apps/journal-watch/journalwatch.caddy      # no curly braces in comments
bash scaffold/docker/render-caddy-routes.sh
git add .generated apps docker-compose.yml
git commit -m "Describe the route change"
git push
ssh journal-watch-vps ./deploy
```

If Caddy fails to start, read its logs first:

```bash
docker compose logs --tail=100 caddy
```

Two things to check: a site block for a hostname without DNS (endless ACME
retries — remove the block) and a file Caddy cannot read (Caddy is uid
65531; `./deploy` normalises permissions, so re-run it rather than
`docker compose up` by hand).

## 6. AWS login (local machine)

```bash
aws configure list-profiles
aws sso login --profile <profile>        # if you use SSO
```

`botocore.exceptions.ProfileNotFound` means the named profile does not exist
locally — list profiles rather than guessing. Long-lived keys in the default
profile work without `--profile`.

## 7. Provision or update AWS resources (local machine)

The script needs boto3; use a venv, not the system Python.

```bash
cd ~/Documents/developer/spanza_journal_watch
python3 -m venv /tmp/jw-ops-venv && source /tmp/jw-ops-venv/bin/activate
pip install boto3

# production
python deploy/bootstrap/aws_setup.py \
  --profile default \
  --bucket spanza-journal-watch-production-v2 \
  --domain journalwatch.org.au \
  --webhook-secret "$(grep '^WEBHOOK_SECRET=' /path/to/apps/journal-watch/.env | cut -d= -f2-)" \
  --enable-inbound

# staging: separate IAM users / topic / config set, reuse the verified SES identity
python deploy/bootstrap/aws_setup.py \
  --profile default \
  --bucket spanza-journal-watch-staging-150064991851 \
  --domain staging.journalwatch.org.au \
  --ses-domain journalwatch.org.au \
  --suffix staging
```

Notes:

- `--planka-bucket` defaults to `<bucket>-planka` and `--backup-bucket` to
  `<bucket>-backups`; pass them explicitly if the existing buckets are named
  differently.
- The script is idempotent. If an IAM user already has access keys it
  re-applies the policy but does **not** mint new keys.
- Read `deploy/bootstrap/aws_setup.py` before running any other `aws` CLI
  mutation against SES, SNS or IAM. Ad-hoc changes to the event destination
  have broken the webhook before.

See [aws-setup.md](aws-setup.md) for what each resource is.

## 8. Restore from Restic (the normal restore path)

Backups are taken hourly by the scaffold's backup role. Each service has its
own repository and passphrase; the service names are `journal-watch` and
`planka` (from `backup/services/*.env`). Config on the host lives in
`/etc/restic/` (root-only), so use `sudo`.

List snapshots:

```bash
ssh journal-watch-vps
sudo /opt/backup/restore.sh --service journal-watch --list
sudo /opt/backup/restore.sh --service planka --list
```

Safe restore into a throw-away database (does not touch the live DB):

```bash
sudo /opt/backup/restore.sh --service journal-watch --target journal_watch_restored
cd /opt/deploy
docker compose exec -T jw_postgres sh -c 'psql -U "$POSTGRES_USER" -d journal_watch_restored -c "select count(*) from submissions_review;"'
docker compose exec -T jw_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "drop database journal_watch_restored;"'   # when done
```

Restore a specific snapshot:

```bash
sudo /opt/backup/restore.sh --service journal-watch --snapshot <id> --target journal_watch_restored
```

Live restore (destructive — last resort). Stop the clients first so the
script can rename the live database for rollback; keep `jw_postgres`
running:

```bash
cd /opt/deploy
docker compose stop journal-watch jw_celeryworker
sudo /opt/backup/restore.sh --service journal-watch        # prompts for confirmation
docker compose up -d journal-watch jw_celeryworker
```

For Planka, stop `jw_planka` instead and use `--service planka`.

Other flags: `--dry-run` prints the commands; `--no-files` restores the
database only. Run a backup now, or verify the repository, with:

```bash
sudo systemctl start backup.service && journalctl -u backup.service -f
sudo systemctl start backup-verify.service && journalctl -u backup-verify.service -f
```

## 9. Import a plain SQL dump (rare)

Use this only for a dump that did not come from Restic (for example a
pre-migration `.sql.gz`). `deploy/bootstrap/migrate_postgres.sh` in this repo
snapshots the current data volume, drops it, starts a fresh Postgres 17
volume and imports the sanitised dump. It stops the whole Compose project
while it runs.

Point it at the **root** compose file so it operates on the live `deploy`
project rather than creating a second one:

```bash
scp deploy/bootstrap/migrate_postgres.sh journal-watch-vps:/tmp/
ssh journal-watch-vps
cd /opt/deploy
bash /tmp/migrate_postgres.sh \
  --compose-file /opt/deploy/docker-compose.yml \
  --env-file /opt/deploy/apps/journal-watch/.env \
  --service jw_postgres \
  /path/to/backup.sql.gz
./deploy                                             # bring everything back up
```

Verify from `/opt/deploy`:

```bash
docker compose exec -T jw_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  select
    (select count(*) from users_user) as users,
    (select count(*) from submissions_article) as articles,
    (select count(*) from submissions_review) as reviews,
    (select count(*) from submissions_issue) as issues,
    (select count(*) from newsletter_subscriber) as subscribers,
    (select count(*) from layout_homepage) as homepages;"'
```

If the homepage errors after an import, check that a homepage row is marked
current:

```bash
docker compose exec -T jw_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  select id, issue_id, publication_ready, created from layout_homepage order by created desc;"'
```

## 10. Planka setup and OIDC checks

```bash
cd /opt/deploy
docker compose exec -T journal-watch python manage.py setup_planka_oidc
docker compose exec -T journal-watch python manage.py setup_planka_api_key
docker compose exec -T journal-watch python manage.py shell -c "
from oauth2_provider.models import Application
print(list(Application.objects.values('name', 'client_id', 'redirect_uris')))"
```

After enabling `OIDC_ENFORCED=true` in the app env:

```bash
docker compose up -d jw_planka
docker compose logs --tail=100 jw_planka
```

## 11. Fast checks after a deploy or restore

```bash
cd /opt/deploy
docker compose ps
docker compose logs --tail=50 journal-watch jw_planka

curl -I https://journalwatch.org.au/
curl -I https://journalwatch.org.au/static/js/color-modes.js
curl -sI "https://journalwatch.org.au$(curl -s https://journalwatch.org.au/ | grep -o '/static/webpack_bundles/css/project\.[0-9a-f]*\.css' | head -1)"
```

Expected: hashed webpack assets carry
`Cache-Control: public, max-age=31536000, immutable`; un-hashed static files
carry `max-age=3600`; compression is applied by Caddy.

## 12. Hurdles we have actually hit

### Compose project mismatch

Cause: commands run from `/opt/deploy/apps/journal-watch`. Fix: always
`cd /opt/deploy`. See "The most important gotcha".

### Env edit ignored

Cause: `docker compose restart`. Fix: `docker compose up -d <service>` and
confirm with `docker compose exec -T <service> env | grep VAR`.

### Site down after a scaffold bump

Cause (2026-09-02): the scaffold moved to a hardened Caddy (non-root uid,
generated route bundle, per-app proxy networks) and the app layout was not
migrated with it. Fix: the migration in server repo commit `15e566c` —
service renamed to `journal-watch`, `.generated/caddy/` rendered and
committed, Planka on the generated proxy network. Always read
`scaffold/UPGRADING.md` before bumping.

### Caddy retrying ACME every few minutes

Cause: a site block for `flower.journalwatch.org.au`, which has no DNS
record. Fix: block removed (server repo `121d084`). Never add a site block
for a name that is not in DNS.

### Planka writing into the Django bucket

Cause: Planka uses prefixes beyond `attachments/*`, so a shared bucket made
IAM brittle. Fix: dedicated bucket in `PLANKA_S3_BUCKET` with its own IAM
user. The old error looked like
`AccessDenied ... s3:PutObject ... /protected/background-images/...`.

### Planka SSO broken after an image bump

Cause: newer Planka releases change the OIDC flow. Fix: keep the digest pin
`2.1.0@sha256:32c919d9…` in `apps/journal-watch/docker-compose.yml`; only
bump with a full SSO re-test.

### Wrong AWS profile name

`botocore.exceptions.ProfileNotFound` — run `aws configure list-profiles`
and use a real one.

### Restored DB looked empty

Cause: checks were run against the wrong Compose project or a throw-away
`--target` database. Fix: verify from `/opt/deploy` with `docker compose ps`
and name the database explicitly in `psql -d`.
