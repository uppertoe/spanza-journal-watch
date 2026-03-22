---
name: Authentik/Planka/Django SSO integration
description: Architecture, decisions, and status of the Authentik OIDC provider replacing Authelia
type: project
---

Authelia has been replaced with Authentik as the OIDC identity provider.

**Why:** Authelia's file-based backend has no self-registration support, making the contributor invite flow unworkable for external contributors.

**Architecture:**

- Authentik runs under `profiles: ['authentik']` on port 9000 (server + worker + dedicated postgres + redis)
- Django allauth OIDC provider_id changed from `"authelia"` to `"authentik"`
- OIDC server URL: `http://host.docker.internal:9000/application/o/spanza-django/`
- Planka OIDC issuer: `http://host.docker.internal:9000/application/o/planka/`

**Bootstrap command:** `python manage.py bootstrap_authentik`

- Idempotent — creates Django OIDC provider, Planka OIDC provider, and contributor enrollment flow
- Must be run after Authentik first starts
- Authentik admin UI: http://localhost:9000/if/admin/ (akadmin / changeme)

**Contributor invite flow:**

- When an invite is created, `_create_authentik_invitation()` in views.py calls the Authentik API to create an enrollment invitation
- The enrollment URL (http://localhost:9000/if/flow/contributor-enrollment/?itoken=<uuid>) is included in the invite email
- Contributors: click enrollment URL → create account in Authentik → click accept URL → OIDC auto-login → invite accepted

**Key credentials (local dev, hardcoded):**

- Bootstrap token / API token: `local-bootstrap-api-token`
- Django OIDC client_id: `spanza-django-local` / secret: `django-local-oidc-secret-changeme`
- Planka OIDC client_id: `planka-local` / secret: `planka-local-oidc-secret-changeme`

**New files:**

- `spanza_journal_watch/backend/authentik.py` — AuthentikClient
- `spanza_journal_watch/backend/management/commands/bootstrap_authentik.py`
- `.envs/.local/.authentik`
- Migration 0024: adds `authentik_invitation_id` to IssueContributorInvite

**How to apply:** After pulling, run: `docker compose -f local.yml --profile authentik up -d` then `manage.py bootstrap_authentik`.
