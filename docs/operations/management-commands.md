# Management Commands

All commands should be run inside the Django container:

```bash
docker exec spanza_journal_watch_local_django /entrypoint python manage.py <command>
```

In production, replace the container name with the production container.

---

## Content & Tagging

### `refresh_mesh_terms`

Re-fetch PubMed metadata for articles missing MeSH terms, then auto-tag from mappings.

```bash
python manage.py refresh_mesh_terms [--batch-size 200] [--limit 0] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--batch-size` | 200 | PMIDs per PubMed API request (max 200) |
| `--limit` | 0 | Max articles to process (0 = all) |
| `--dry-run` | off | Report counts without fetching |

### `auto_tag_articles`

Apply MeSH-to-tag mappings to all articles with MeSH metadata. Idempotent.

```bash
python manage.py auto_tag_articles
```

### `compute_tag_clusters`

Compute tag co-occurrence clusters for the Explore page. Results cached for 1 week. Also runs weekly via Celery Beat (Sunday 5am UTC).

```bash
python manage.py compute_tag_clusters [--threshold 0.5] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--threshold` | 0.5 | Similarity threshold (0-1) for clustering |
| `--dry-run` | off | Print clusters without caching |

### `match_review_articles`

Match PubmedArticles missing PMIDs to PubMed records and deduplicate. Resolves DOIs via NCBI, fills metadata from CrossRef, and merges duplicate article records.

```bash
python manage.py match_review_articles [--apply] [--reviews-only]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply` | off | Apply changes (default is dry run) |
| `--reviews-only` | off | Only process articles linked to reviews |

### `backfill_article_metadata`

Re-fetch metadata from PubMed for articles missing citation fields (authors, volume, etc.).

```bash
python manage.py backfill_article_metadata [--dry-run] [--limit 0] [--batch-size 50]
```

---

## Journal Browser

### `backfill_pubmed_journal_cache`

Backfill cached PubMed journal articles for watched journals over a month range.

```bash
python manage.py backfill_pubmed_journal_cache [--from-month YYYY-MM] [--to-month YYYY-MM] [--journal ID]
```

| Flag | Description |
|------|-------------|
| `--from-month` | Start month (YYYY-MM format) |
| `--to-month` | End month (YYYY-MM format) |
| `--journal` | Watched journal PK (repeatable) |

### `backfill_watched_journals`

Backfill watched journal metadata from NLM catalog and remove mismatched article links.

```bash
python manage.py backfill_watched_journals [--apply] [--journal ID] [--skip-metadata] [--skip-cleanup]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--apply` | off | Apply changes (default is dry run) |
| `--journal` | all | Watched journal PK (repeatable) |
| `--skip-metadata` | off | Skip NLM catalog backfill |
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
| `--sync` | Run inline instead of queuing Celery tasks |

Targets: FeatureArticle images, Issue images, HealthService logos, Author profile images, Review feature images.

### `backfill_issue_images`

Copy issue images from FeatureArticle into Issue.image for issues missing images.

```bash
python manage.py backfill_issue_images [--dry-run]
```

---

## Newsletter & Email

### `preview_emails`

Send preview copies of all email templates to mailpit for visual review.

```bash
python manage.py preview_emails [--to preview@example.com]
```

Sends 18 template previews. Requires mailpit running at `http://localhost:8025`.

### `check_email_auth`

Verify SPF, DKIM, and DMARC DNS records for the newsletter sending domain.

```bash
python manage.py check_email_auth [--domain example.com]
```

Defaults to the domain from `NEWSLETTER_FROM_EMAIL`. Checks SPF includes for Amazon SES, DKIM selectors, and DMARC policy.

---

## Planka Integration

### `setup_planka_oidc`

Register Planka as an OAuth2/OIDC application in Django. Idempotent.

```bash
python manage.py setup_planka_oidc
```

Requires `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and optionally `PLANKA_EXTERNAL_URL` environment variables.

### `setup_planka_api_key`

Bootstrap a Planka API key via direct database writes.

```bash
python manage.py setup_planka_api_key [--email admin@example.com]
```

Requires `PLANKA_DB_URL` environment variable. Idempotent (re-running rotates the key).

---

## User Management

### `create_chief_editor`

Create or promote a user to chief editor with all editorial permissions.

```bash
python manage.py create_chief_editor email@example.com [--name "Full Name"] [--password]
```

Grants: chief_editor, manage_issue_builder, manage_subscriber_csv, send_newsletters, view_newsletter_stats, view_site_analytics.

---

## Testing & Fixtures

### `generate_journal_browser_fixture`

Generate a sample fixture for the journals browser with demo data.

```bash
python manage.py generate_journal_browser_fixture [--fixture-output path/to/output.json]
```

### `generate_regression_baseline`

Generate anonymized regression fixtures and HTML snapshots from the current database.

```bash
python manage.py generate_regression_baseline [--fixture-output path] [--snapshot-dir path] [--manifest-output path]
```

---

## Common Workflows

### After deploying tag changes

```bash
python manage.py refresh_mesh_terms
python manage.py auto_tag_articles
python manage.py compute_tag_clusters
```

### Setting up a new environment

```bash
python manage.py setup_planka_oidc
python manage.py setup_planka_api_key
python manage.py create_chief_editor admin@example.com --name "Chief Editor"
python manage.py backfill_watched_journals --apply
python manage.py backfill_pubmed_journal_cache
python manage.py refresh_mesh_terms
python manage.py auto_tag_articles
python manage.py compute_tag_clusters
```
