# Management Commands

Run every command inside the Django container.

Production (from the Compose project root on the host):

```bash
ssh journal-watch-vps
cd /opt/deploy
docker compose exec -T journal-watch python manage.py <command>
```

Local development:

```bash
docker exec spanza_journal_watch_local_django /entrypoint python manage.py <command>
```

`migrate` and `collectstatic` are run by the deploy hook on every
`./deploy` and do not need to be run by hand in production.

---

## Scheduled tasks

Celery Beat runs inside `jw_celeryworker` (`celery worker --beat`) using the
database scheduler, so schedules live in the `django_celery_beat` tables and
are editable from the Django admin. Two of the commands below also run on a
schedule created by migrations:

| Task | Schedule (UTC) |
|------|----------------|
| Refresh missing MeSH terms (`refresh_mesh_terms`) | Sunday 04:00 |
| Compute tag co-occurrence clusters (`compute_tag_clusters`) | Sunday 05:00 |

---

## Content & Tagging

### `refresh_mesh_terms`

Re-fetch PubMed metadata for articles missing MeSH terms, then auto-tag from mappings.

```bash
python manage.py refresh_mesh_terms [--batch-size 200] [--limit 0] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--batch-size` | 200 | PMIDs per PubMed efetch request (max 200) |
| `--limit` | 0 | Max articles to process (0 = all) |
| `--dry-run` | off | Report counts without fetching |

### `auto_tag_articles`

Apply MeSH-to-tag mappings to all articles with MeSH metadata. Idempotent. No flags.

```bash
python manage.py auto_tag_articles
```

### `compute_tag_clusters`

Compute tag co-occurrence clusters for the Explore page and cache the result.

```bash
python manage.py compute_tag_clusters [--threshold 0.6] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--threshold` | 0.6 | Similarity threshold (0-1) for clustering |
| `--dry-run` | off | Print clusters without caching |

### `match_review_articles`

Match PubmedArticles missing PMIDs to PubMed records and deduplicate. Resolves DOIs via NCBI, fills metadata from CrossRef, and merges duplicate article records.

```bash
python manage.py match_review_articles [--apply] [--reviews-only]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply` | off | Apply changes (default is a dry run) |
| `--reviews-only` | off | Only process articles linked to reviews |

### `backfill_article_metadata`

Re-fetch metadata from PubMed for articles missing citation fields (authors, volume, etc.).

```bash
python manage.py backfill_article_metadata [--dry-run] [--limit 0] [--batch-size 50]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Report without writing |
| `--limit` | 0 | Max articles to process (0 = all) |
| `--batch-size` | 50 | PMIDs per PubMed request |

---

## Journal Browser

### `backfill_pubmed_journal_cache`

Backfill cached PubMed journal articles for watched journals over a month range.

```bash
python manage.py backfill_pubmed_journal_cache [--from-month YYYY-MM] [--to-month YYYY-MM] [--journal ID]
```

| Flag | Description |
|------|-------------|
| `--from-month` | Start month (YYYY-MM); must not be after `--to-month` |
| `--to-month` | End month (YYYY-MM) |
| `--journal` | Watched journal PK (repeatable; default all) |

### `backfill_watched_journals`

Backfill watched journal identifiers from the NLM catalog and remove mismatched article links.

```bash
python manage.py backfill_watched_journals [--apply] [--journal ID] [--skip-metadata] [--skip-cleanup]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply` | off | Apply changes (default is a dry run) |
| `--journal` | all | Watched journal PK (repeatable) |
| `--skip-metadata` | off | Skip the NLM catalog backfill |
| `--skip-cleanup` | off | Skip mismatched article cleanup |

---

## Images

### `reprocess_uploaded_images`

Reprocess all uploaded frontend-facing images through the Pillow/WebP pipeline.

```bash
python manage.py reprocess_uploaded_images [--sync]
```

| Flag | Description |
|------|-------------|
| `--sync` | Run inline instead of queueing Celery tasks |

Targets: `layout.FeatureArticle.image`, `submissions.Issue.image`,
`submissions.HealthService.logo`, `submissions.Author.profile_image`,
`submissions.Review.feature_image`.

### `backfill_issue_images`

Copy issue images from the layout FeatureArticle model into `Issue.image` for issues missing images.

```bash
python manage.py backfill_issue_images [--dry-run]
```

---

## Newsletter & Email

### `preview_emails`

Send preview copies of all email templates (18 messages) to mailpit for visual review. Local development only — requires mailpit at `http://localhost:8025`.

```bash
python manage.py preview_emails [--to preview@example.com]
```

### `check_email_auth`

Verify SPF, DKIM and DMARC DNS records for the newsletter sending domain.

```bash
python manage.py check_email_auth [--domain example.com]
```

Defaults to the domain of `NEWSLETTER_FROM_EMAIL`. Checks the SPF include
for Amazon SES, the DKIM selectors, and the DMARC policy.

---

## Planka Integration

### `setup_planka_oidc`

Register Planka as an OAuth2/OIDC client application in django-oauth-toolkit. Idempotent: creates the application if missing, otherwise updates any drifted field.

```bash
python manage.py setup_planka_oidc
```

Reads `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `PLANKA_EXTERNAL_URL` from the environment (the redirect URI is `<PLANKA_EXTERNAL_URL>/oidc-callback`). Without them it falls back to local-development defaults, so in production make sure the app env is loaded — it is when run through `docker compose exec`.

### `setup_planka_api_key`

Generate a Planka API key and initialise the Planka instance by writing directly to Planka's Postgres. Works whether or not `OIDC_ENFORCED` is set and whether or not Planka is running. Idempotent: re-running rotates the stored key.

```bash
python manage.py setup_planka_api_key [--email admin@example.com]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--email` | `PLANKA_ADMIN_EMAIL` | Planka admin account to attach the key to |

Requires `PLANKA_DB_URL` (set by the production compose file to Planka's
database) and `PLANKA_ADMIN_EMAIL`. The same action is available from the
backend settings page (`/editorial/settings`, **Run setup_planka_api_key**).

---

## User Management

### `create_chief_editor`

Create or promote a user to chief editor with all editorial permissions. Prompts for a password when creating a new account.

```bash
python manage.py create_chief_editor email@example.com [--name "Full Name"] [--password]
```

| Flag | Description |
|------|-------------|
| `--name` | Full name for a new account |
| `--password` | Prompt for a new password even if the account already exists |

Grants `submissions.chief_editor`, `submissions.manage_issue_builder`,
`backend.manage_subscriber_csv`, `backend.send_newsletters`,
`backend.view_newsletter_stats`, `backend.view_site_analytics`, and sets
`is_staff`.

### `activate_invited_contributors`

Activate contributors still in `INVITED` status whose email matches an existing user account — the case where a reviewer created an account from the invite link but the redirect back to the acceptance page failed. Replays the acceptance: links the user, sets `ACTIVE`, grants permissions (plus coordinator permissions and `is_staff` for coordinators), links an Author profile by email, marks the invite consumed, marks the email verified, and syncs the contributor to Planka.

```bash
python manage.py activate_invited_contributors [--apply]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply` | off | Apply changes (default prints a dry-run report) |

---

## Testing & Fixtures

### `generate_journal_browser_fixture`

Generate a sample fixture for the journals browser and cached PubMed workflow.

```bash
python manage.py generate_journal_browser_fixture [--fixture-output spanza_journal_watch/fixtures/journal_browser_sample.json]
```

### `generate_regression_baseline`

Generate anonymised regression fixtures and HTML snapshots from the current local database.

```bash
python manage.py generate_regression_baseline \
  [--fixture-output spanza_journal_watch/fixtures/regression_baseline.json] \
  [--snapshot-dir tests/regression/snapshots] \
  [--manifest-output tests/regression/snapshots/manifest.json]
```

---

## SEO

### `indexnow_submit`

Submit URLs to [IndexNow](https://www.indexnow.org/) so Bing, Yandex and other participating engines re-crawl them promptly. Requires `INDEXNOW_KEY` (the key is served at `/<key>.txt`). Publishing an issue from the backend already queues a submission for the issue, its reviews and their authors; this command covers everything else.

```bash
python manage.py indexnow_submit                # every URL in the sitemap
python manage.py indexnow_submit /reviews/foo   # specific paths or absolute URLs
python manage.py indexnow_submit --dry-run      # list what would be sent
```

---

## Common Workflows

### After deploying tag-mapping changes

```bash
python manage.py refresh_mesh_terms
python manage.py auto_tag_articles
python manage.py compute_tag_clusters
```

### Bootstrapping a new environment

Run after the first `./deploy` has migrated the database:

```bash
python manage.py create_chief_editor admin@example.com --name "Chief Editor"
python manage.py setup_planka_oidc
python manage.py setup_planka_api_key
python manage.py backfill_watched_journals --apply
python manage.py backfill_pubmed_journal_cache
python manage.py refresh_mesh_terms
python manage.py auto_tag_articles
python manage.py compute_tag_clusters
```
